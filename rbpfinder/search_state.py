"""M19-d -- persistent per-search state. THIS is the recovery source.

The event log (`events.py`) shows what happened; it is explicitly not consulted here.
This file answers one question and it must be answerable after a power cut:

    for this SearchKey, is there a trusted completed result -- and if not, is there a
    live job we should poll instead of submitting again?

Field report: a run was interrupted with twelve raw results already on disk, and the next
run resubmitted all twelve. Worse, any job still RUNNING at the moment of interruption was
lost entirely -- the id existed only inside a function that never returned.

Two rules the state machine exists to enforce:

    a search is re-submitted only when nothing trustworthy is known about it
    FAILED is not SEARCHED_NO_HIT, ever, in any direction
"""
import hashlib
import json
import os
import pathlib
import tempfile
import time

# States. SUBMITTED and RUNNING are kept apart deliberately: if the POST succeeded, the
# checkpoint was written, and the process died before the first poll, we still hold a
# job id and must poll rather than submit again.
PENDING = "pending"
SUBMITTED = "submitted"
RUNNING = "running"
SEARCHED_HITS = "searched_hits"
SEARCHED_NO_HIT = "searched_no_hit"
FAILED = "failed"

# Reusable without contacting anything. Note what is NOT here: FAILED. A failure is not
# a result, and treating it as one is how a transport outage becomes "we looked and
# found nothing".
TRUSTED_COMPLETE = (SEARCHED_HITS, SEARCHED_NO_HIT)
RESUMABLE = (SUBMITTED, RUNNING)

# Why a search failed decides whether a later run may try again. "It failed once" is not
# a reason to never look again, and it is not a reason to loop forever either.
RETRYABLE = "transport_retryable"
TERMINAL = "provider_terminal"
INVALID = "invalid_request"


# A manifest row is (cds_id, kind, path). It names no taxonomy, because Stage 2 keeps
# ONE S unit per CDS however many databases answered. So manifest trust is declared at
# CDS level under this sentinel rather than guessed onto a taxonomy the file never
# mentioned -- claiming a host-arm search happened because a phage-arm result was
# imported would be inventing scope.
MANIFEST_SCOPE = "manifest"


class TrustConflict(Exception):
    """Two trusted sources disagree about the same search. Never resolved silently."""


def search_key(cds_id, taxonomy, sequence_sha256):
    """Identity of one search. All three parts matter.

    The sequence hash is in here so that a re-annotation which changes a protein cannot
    silently reuse the old result under the same locus tag -- same cds_id, different
    sequence, different search.
    """
    blob = "%s|%s|%s" % (cds_id, taxonomy, sequence_sha256)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def sequence_sha256(seq):
    return hashlib.sha256((seq or "").encode("utf-8")).hexdigest()


class SearchState(object):
    """One JSON file, rewritten atomically on every transition.

    Atomic because the alternative was observed in the field: a process killed mid-write
    leaves a truncated file, and a truncated state file that is *parsed leniently* is
    worse than none -- it silently drops the searches whose lines were lost. Here a
    corrupt file is refused, loudly, and the last good published state is kept.
    """

    VERSION = 1

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.entries = {}
        self.corrupt = False
        self.corrupt_reason = ""
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as exc:
            # Refuse, never guess. A half-written checkpoint that we "recover what we
            # can" from is exactly how completed work gets resubmitted and, worse, how
            # unfinished work gets treated as done.
            self.corrupt = True
            self.corrupt_reason = "checkpoint is not valid JSON: %s" % exc
            return
        if not isinstance(data, dict) or data.get("version") != self.VERSION:
            self.corrupt = True
            self.corrupt_reason = ("checkpoint version is %r, expected %d"
                                   % (data.get("version") if isinstance(data, dict)
                                      else "?", self.VERSION))
            return
        self.entries = data.get("searches") or {}

    def _write(self):
        """Write to a temp file in the same directory, fsync, then replace.

        os.replace is atomic on Windows and POSIX. A reader therefore sees either the
        previous complete state or the new complete state, never a partial one.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": self.VERSION, "updated_at": _now(),
                   "searches": self.entries}
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, str(self.path))
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # -- transitions -------------------------------------------------------
    def _set(self, key, **fields):
        entry = self.entries.setdefault(key, {})
        entry.update(fields)
        entry["updated_at"] = _now()
        self._write()               # every transition, not once at the end
        return entry

    def declare(self, key, cds_id, taxonomy, sequence_sha):
        if key in self.entries:
            return self.entries[key]
        return self._set(key, cds_id=cds_id, taxonomy=taxonomy,
                         sequence_sha256=sequence_sha, state=PENDING,
                         attempt_count=0, job_id=None)

    def submitted(self, key, job_id):
        """Persisted BEFORE the first poll. This is the whole point of the split."""
        entry = self.entries.get(key, {})
        return self._set(key, state=SUBMITTED, job_id=job_id,
                         attempt_count=entry.get("attempt_count", 0) + 1)

    def running(self, key):
        return self._set(key, state=RUNNING)

    def completed(self, key, has_hits, raw_response_path=None, database_release=None):
        return self._set(key,
                         state=SEARCHED_HITS if has_hits else SEARCHED_NO_HIT,
                         raw_response_path=(str(raw_response_path)
                                            if raw_response_path else None),
                         database_release=database_release)

    def failed(self, key, error_class, failure_kind=RETRYABLE):
        return self._set(key, state=FAILED, error_class=error_class,
                         failure_kind=failure_kind)

    def manifest_trusted(self, cds_id, sequence_sha):
        """Is there CDS-level trusted sequence evidence, from a manifest?"""
        key = search_key(cds_id, MANIFEST_SCOPE, sequence_sha)
        return self.state_of(key) in TRUSTED_COMPLETE

    def import_manifest(self, cds_id, sequence_sha, has_hits, source="stage2_manifest"):
        """Seed the trusted cache from a manifest row.

        Refuses to overwrite a disagreeing checkpoint. If a previous run searched this
        protein and found hits while the manifest says it found none, something is wrong
        with one of them and picking a winner would bury it.
        """
        key = search_key(cds_id, MANIFEST_SCOPE, sequence_sha)
        want = SEARCHED_HITS if has_hits else SEARCHED_NO_HIT
        have = self.state_of(key)
        if have in TRUSTED_COMPLETE and have != want:
            raise TrustConflict(
                "%s: the checkpoint says %s and %s says %s for the same sequence. "
                "Two trusted sources disagree; refusing to choose one."
                % (cds_id, have, source, want))
        return self._set(key, cds_id=cds_id, taxonomy=MANIFEST_SCOPE,
                         sequence_sha256=sequence_sha, state=want,
                         imported_from=source, job_id=None)

    def import_trusted(self, key, cds_id, taxonomy, sequence_sha, has_hits, source):
        """A completed result that came from somewhere else -- a manifest, say.

        Recovery never asks "was this a manifest or a checkpoint". It asks whether a
        trusted completed result exists for this SearchKey. A manifest is one source of
        trust among others, not a separate scientific path.
        """
        return self._set(key, cds_id=cds_id, taxonomy=taxonomy,
                         sequence_sha256=sequence_sha,
                         state=SEARCHED_HITS if has_hits else SEARCHED_NO_HIT,
                         imported_from=source, job_id=None)

    # -- queries -----------------------------------------------------------
    def state_of(self, key):
        return (self.entries.get(key) or {}).get("state")

    def trusted_completed(self):
        return {k for k, e in self.entries.items() if e.get("state") in TRUSTED_COMPLETE}

    def resumable(self):
        """Searches with a live job id: poll these, never re-POST them."""
        return {k: e for k, e in self.entries.items()
                if e.get("state") in RESUMABLE and e.get("job_id")}

    def missing_delta(self, required_keys, retry_failed=False):
        """required - trusted = what still has to be searched.

        One computation, whatever the sources were. Stage 6 reopening a candidate simply
        adds a key to `required`; it does not need a special path.
        """
        trusted = self.trusted_completed()
        missing = set(required_keys) - trusted
        if not retry_failed:
            # A failure is not a result, but re-attempting it is a policy decision, not
            # an automatic one -- an invalid request will fail identically forever.
            missing -= {k for k, e in self.entries.items()
                        if e.get("state") == FAILED
                        and e.get("failure_kind") in (TERMINAL, INVALID)}
        return missing

    def summary(self):
        by = {}
        for entry in self.entries.values():
            by[entry.get("state")] = by.get(entry.get("state"), 0) + 1
        return {"total": len(self.entries),
                "searched_hits": by.get(SEARCHED_HITS, 0),
                "searched_no_hit": by.get(SEARCHED_NO_HIT, 0),
                "failed": by.get(FAILED, 0),
                "resumable": len(self.resumable()),
                "pending": by.get(PENDING, 0)}


    def archive(self, reason):
        """Move the current state aside instead of deleting it.

        `--restart-stage2` means "search again", not "destroy the evidence of what
        happened last time". The 176 MB that a rerun once erased is why this project
        does not have a code path that removes completed work (HANDOFF 9.3).
        """
        if not self.path.exists():
            return None
        stamp = time.strftime("%Y%m%dT%H%M%S")
        dest = self.path.with_name("%s.%s.archived.json" % (self.path.stem, stamp))
        self.path.rename(dest)
        self.entries = {}
        self.corrupt = False
        self._write()
        return dest


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")
