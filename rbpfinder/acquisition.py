"""How a required database becomes a verified local BLAST database.

M15C-1 answered "I already have one -- register it". This module answers "I do not have
one -- how do I get it", and it deliberately knows nothing about biology. The split is
frozen in `database_acquisition.separation`:

    WHAT database is needed   ->  DatabaseSpec      (biology decides)
    HOW to obtain it          ->  AcquisitionPlan   (this module decides)

Three semantics carry the whole design, and each of them exists because of a specific
way this project has already been bitten.

**Method choice is authority, not preference.** `--method direct` that cannot run fails;
it never quietly becomes cursor pagination. Same rule as the executable resolver and the
database precedence -- if a user names something, a wrong answer must be reported, not
routed around.

**Transport unavailability may fall back; integrity failure must not.** A 404 says
nothing about the data. A checksum mismatch says the bytes are wrong, and re-fetching
them by another route would present real corruption as a network quirk and hand back a
database that looks successfully acquired. This is the invariant of M15C-2.

**The database in use is never a download target.** Everything lands in staging, is
validated, is built, is validated again, and only then is published atomically. A
completed 176 MB download was once destroyed by a command that wrote where it should
only have read; acquisition is where that mistake is easiest to repeat.
"""
import hashlib
import json
import pathlib
import shutil

from .errors import CapabilityUnavailable, InputValidationError, RBPFinderError

METHODS = ("existing", "direct_compressed", "rest_stream", "cursor_pagination")
AUTO_ORDER = METHODS                       # frozen in database_acquisition.priority

STATES = ("PLANNED", "STAGING", "FETCHED", "FETCH_VALIDATED", "BUILT",
          "BUILD_VALIDATED", "PUBLISHED", "FAILED")


class TransportUnavailable(CapabilityUnavailable):
    """This route cannot run here. MAY trigger fallback under --method auto."""


class IntegrityFailure(RBPFinderError):
    """The bytes are wrong. Must NEVER trigger fallback."""
    exit_code = 2


class DatabaseSpec(object):
    """WHAT is needed. Contains no notion of how to fetch it."""

    def __init__(self, role, biological_query, taxonomy=None, host_context=None,
                 release_or_snapshot=None, expected_source=None, expected_records=None,
                 checksum=None):
        self.role = role
        self.biological_query = biological_query
        self.taxonomy = taxonomy
        self.host_context = host_context
        self.release_or_snapshot = release_or_snapshot
        self.expected_source = expected_source
        self.expected_records = expected_records
        self.checksum = checksum

    @property
    def spec_hash(self):
        """Identity of the REQUEST, so a resume can never splice two datasets."""
        payload = json.dumps({"role": self.role, "query": self.biological_query,
                              "taxonomy": self.taxonomy,
                              "release": self.release_or_snapshot},
                             sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def describe(self):
        return {"role": self.role, "biological_query": self.biological_query,
                "taxonomy": self.taxonomy, "host_context": self.host_context,
                "release_or_snapshot": self.release_or_snapshot,
                "expected_source": self.expected_source,
                "spec_hash": self.spec_hash}


class AcquisitionPlan(object):
    """HOW it will be obtained. Produced by the resolver, executed by the runner."""

    def __init__(self, method, source, destination, resumable, verification_level,
                 reason="", skipped=None):
        self.method = method
        self.source = source
        self.destination = destination
        self.resumable = resumable
        self.verification_level = verification_level
        self.reason = reason
        self.skipped = skipped or []          # [(method, why)] -- the audit trail

    def describe(self):
        return {"method": self.method, "source": self.source,
                "destination": str(self.destination), "resumable": self.resumable,
                "verification_level": self.verification_level,
                "reason": self.reason,
                "methods_skipped": [{"method": m, "why": w} for m, w in self.skipped]}


class AcquisitionResolver(object):
    """Chooses the plan. Never fetches anything itself."""

    def __init__(self, transports, existing_lookup=None):
        # {method_name: transport}; a transport answers available(spec) and fetch(...)
        self.transports = transports
        self.existing_lookup = existing_lookup

    def _existing(self, spec):
        if not self.existing_lookup:
            return None
        prefix, status, source, detail = self.existing_lookup(spec.role)
        if status == "AVAILABLE":
            return AcquisitionPlan("existing", prefix, prefix, False,
                                   "already_registered",
                                   reason="configured via %s and still usable: %s"
                                          % (source, detail))
        return None

    def plan(self, spec, method="auto", staging_root=None):
        staging_root = pathlib.Path(staging_root or ".")
        dest = staging_root / ("%s_%s" % (spec.role, spec.spec_hash))

        # An already-usable database means zero network, whatever was asked for.
        # `database acquire` on a machine that already has the database must not
        # re-download it.
        existing = self._existing(spec)
        if existing and method in ("auto", "existing"):
            return existing
        if method == "existing":
            raise TransportUnavailable(
                "no usable database is registered for role %r, and --method existing "
                "forbids fetching one" % spec.role, capability="existing")

        if method != "auto":
            if method not in self.transports:
                raise InputValidationError(
                    "unknown acquisition method %r (expected one of %s)"
                    % (method, ", ".join(METHODS)))
            t = self.transports[method]
            ok, why = t.available(spec)
            if not ok:
                # AUTHORITY: an explicitly requested method that cannot run is the
                # answer, not the first step of a search.
                raise TransportUnavailable(
                    "method %r was requested explicitly and cannot run here: %s"
                    % (method, why), capability=method)
            return AcquisitionPlan(method, t.source(spec), dest, t.resumable,
                                   "blast_verified", reason="explicitly requested")

        skipped = []
        for name in AUTO_ORDER:
            if name == "existing":
                continue
            t = self.transports.get(name)
            if t is None:
                skipped.append((name, "no transport implementation available"))
                continue
            ok, why = t.available(spec)
            if not ok:
                # Only UNAVAILABILITY moves us down the list. Nothing has been
                # downloaded yet, so no integrity judgement can be involved here.
                skipped.append((name, why))
                continue
            return AcquisitionPlan(name, t.source(spec), dest, t.resumable,
                                   "blast_verified",
                                   reason="first available method in the frozen order",
                                   skipped=skipped)
        raise TransportUnavailable(
            "no acquisition method is available for %s: %s"
            % (spec.role, "; ".join("%s (%s)" % (m, w) for m, w in skipped)),
            capability="acquisition")


class Staging(object):
    """The state machine. The published database is only ever written at the end.

    Resume is bound to the spec hash: a staged directory belonging to a different query
    is refused rather than continued, so two datasets can never be spliced into one file
    that merely looks resumable.
    """

    def __init__(self, plan, spec):
        self.plan = plan
        self.spec = spec
        self.dir = pathlib.Path(plan.destination)
        self.state_file = self.dir / "acquisition_state.json"

    def load(self):
        if not self.state_file.exists():
            return None
        state = json.loads(self.state_file.read_text(encoding="utf-8-sig"))
        if state.get("spec_hash") != self.spec.spec_hash:
            raise InputValidationError(
                "staged data in %s belongs to a different request (spec %s, wanted %s); "
                "resuming would mix two datasets"
                % (self.dir, state.get("spec_hash"), self.spec.spec_hash),
                path=str(self.dir))
        return state

    def write(self, state_name, **extra):
        if state_name not in STATES:
            raise ValueError("unknown acquisition state %r" % state_name)
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {"state": state_name, "spec_hash": self.spec.spec_hash,
                   "method": self.plan.method, "spec": self.spec.describe()}
        payload.update(extra)
        self.state_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                                   encoding="utf-8")
        return payload

    def publish(self, built_prefix, final_prefix):
        """Move a fully validated build into place, and only then.

        The final location is untouched until this call, so a failure anywhere earlier
        leaves whatever the user already had exactly as it was.
        """
        state = self.load() or {}
        if state.get("state") != "BUILD_VALIDATED":
            raise IntegrityFailure(
                "refusing to publish from state %r: only a BUILD_VALIDATED staging "
                "directory may be published" % state.get("state"))
        final = pathlib.Path(final_prefix)
        final.parent.mkdir(parents=True, exist_ok=True)
        moved = []
        for src in sorted(pathlib.Path(built_prefix).parent.glob(
                pathlib.Path(built_prefix).name + ".*")):
            dst = final.with_name(final.name + src.name[len(pathlib.Path(built_prefix).name):])
            shutil.move(str(src), str(dst))
            moved.append(str(dst))
        self.write("PUBLISHED", published_to=str(final), files=moved)
        return final, moved


def verification_record(sidecars_present, blastdbcmd_checked, blastdbcmd_result=None):
    """Never a bare `verified: true` -- the two strengths must stay distinguishable."""
    return {
        "blast_sidecars_present": bool(sidecars_present),
        "blastdbcmd_checked": bool(blastdbcmd_checked),
        "blastdbcmd_result": blastdbcmd_result,
        "verification_level": "blast_verified" if blastdbcmd_checked else "structural_only",
    }
