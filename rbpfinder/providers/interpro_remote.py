"""M21-d -- fetching an InterProScan result instead of asking a person to.

That is the whole of it. Every scientific decision about InterPro stays where it was:
which candidates are worth asking about, whether a match is eligible, what direction it
carries, how strong it may be, what it does to a tier. This module submits, polls,
retrieves, and writes a file.

Convergence with the manual path is structural rather than asserted. The raw
InterProScan JSON is written to disk and registered in the manifest under
`interproscan_json` -- the same kind a file handed back by a user gets. From that point
the two paths are not similar; they are the same code reading the same bytes.

**This module never reads the response.** An InterProScan response contains the words
"receptor binding" in plain sight, which makes this the first provider where interpreting
its own download is a concrete temptation rather than a hypothetical one. It writes the
file and stops. `evidence_acquisition.convergence.providers_may_not_interpret` is the
rule; `interpro_remote.provider_may_not` lists it out.

Lifecycle is M19-d's, unchanged and for the same reason:

    PENDING -> SUBMITTED(job_id) -> RUNNING -> RETRIEVED -> PARSED

with a checkpoint after every irreversible step. Submitting is the only one that creates
work on someone else's service, so it is the only one that must never be repeated for a
job that already has an id.
"""
import json
import pathlib
import time

from ..errors import CapabilityUnavailable, InputValidationError
from .. import search_state as sstate

_BASE = "interpro_remote"

EMAIL_REQUIRED = ("EBI requires a contact address for job submission. Pass "
                  "--ebi-email. It identifies the submitter to EBI and is not a "
                  "results address.")


class InterProUnavailable(CapabilityUnavailable):
    """The service did not answer. Never a statement about any protein."""


class PollBudgetExhausted(Exception):
    """We stopped waiting. The job did not fail -- it is still running, with a live id.

    Kept apart from `InterProUnavailable` deliberately. Recording "this run ran out of
    polls" as a FAILED search would drop the job id out of `resumable()`, and the next
    run would submit the same work again -- destroying the exact capability the
    checkpointed lifecycle exists to provide. "We stopped waiting" and "it failed" are
    two different facts, which is the same distinction the evidence records are built on.
    """


def http_transport(endpoint, email=None, attempts=5, backoff=(1, 2, 4, 8),
                   timeout=60):
    """The real EBI InterProScan transport, as three non-blocking primitives.

    Same shape and same retry discipline as `stage2_remote.http_submit`: retry transport
    failures, never answers. A dropped connection says nothing about the query and is
    worth asking again; a 400 or a job in state ERROR is an answer, and retrying it would
    turn a real result into a hang. This machine drops 20-50% of outbound TLS
    connections, and one job is at least three round trips.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    if not email:
        raise InputValidationError(EMAIL_REQUIRED)

    def _retry(what, call):
        last = None
        for i in range(attempts):
            try:
                return call()
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise InterProUnavailable(
                        "EBI %s returned HTTP %d, which is an answer rather than a "
                        "transport failure; not retrying" % (what, exc.code),
                        capability="interproscan")
                last = exc
            except (urllib.error.URLError, OSError) as exc:
                last = exc
            if i < attempts - 1:
                time.sleep(backoff[min(i, len(backoff) - 1)])
        raise InterProUnavailable(
            "EBI %s failed %d times in a row; last error: %s" % (what, attempts, last),
            capability="interproscan")

    def _post(path, data):
        def once():
            req = urllib.request.Request(
                endpoint + path,
                data=urllib.parse.urlencode(data).encode("utf-8"),
                headers={"User-Agent": "RBPfinder", "Accept": "text/plain"})
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                return fh.read().decode("utf-8").strip()
        return _retry("POST " + path, once)

    def _get(path, accept="application/json"):
        def once():
            req = urllib.request.Request(
                endpoint + path,
                headers={"User-Agent": "RBPfinder", "Accept": accept})
            with urllib.request.urlopen(req, timeout=timeout) as fh:
                return fh.read().decode("utf-8")
        return _retry("GET " + path, once)

    def submit_only(sequence):
        """POST the job. The ONLY step that creates work on the service."""
        return _post("/run", {"email": email, "sequence": sequence,
                              "goterms": "false", "pathways": "false"})

    def poll_once(job_id):
        return _get("/status/" + job_id, accept="text/plain").strip()

    def fetch_result(job_id):
        return _get("/result/" + job_id + "/json")

    submit_only.poll_once = poll_once
    submit_only.fetch_result = fetch_result
    return submit_only


class InterProSession(object):
    """Drives the lifecycle for a set of candidates. Owns cadence; owns no science."""

    def __init__(self, rules, artifact_dir, transport, state_path=None, policy=None,
                 authorised=False, sleep=time.sleep):
        self.rules = rules
        self.dir = pathlib.Path(artifact_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.transport = transport
        self.policy = policy
        self.authorised = bool(authorised)
        self.sleep = sleep
        self.state = sstate.SearchState(
            pathlib.Path(state_path or (self.dir / "interpro_search_state.json")))
        self.submitted = self.retrieved = self.failed = 0
        self.pending = 0
        self.reused = 0
        self.limitations = []

    # -- authorisation ----------------------------------------------------
    def disclosure(self):
        return self.rules.get(_BASE + ".authorisation.disclosure")

    def authorise(self):
        """Explicit, per run. Automation is a choice; it is never assumed."""
        if not self.authorised:
            self.limitations.append(
                "InterPro automated mode was not authorised for this run; nothing was "
                "submitted")
            return False
        if self.policy is not None:
            ok, why = self.policy.allows("ebi_interproscan")
            if not ok:
                self.limitations.append("data policy blocked InterPro submission: %s"
                                        % why)
                return False
        return True

    # -- the loop ---------------------------------------------------------
    def _key(self, cds_id, seq):
        # Taxonomy is not part of an InterPro request, so it is fixed rather than
        # omitted: the key shape is shared with the Stage 2 state file and a missing
        # field there would silently collide with a differently-scoped search.
        return sstate.search_key(cds_id, "interpro", sstate.sequence_sha256(seq))

    def _artifact(self, cds_id, seq):
        return self.dir / ("%s.%s.interproscan.json"
                           % (cds_id, sstate.sequence_sha256(seq)[:12]))

    def ensure_scanned(self, records_by_id, selected_ids, max_polls=60,
                       poll_seconds=5):
        """Return {cds_id: raw artefact path} for everything that completed.

        Never blocks in one call per job: submit, checkpoint, then poll. A process killed
        during polling resumes from the checkpointed job id instead of submitting again.
        """
        out = {}
        if not self.authorise():
            return out
        for cds_id in selected_ids or []:
            rec = records_by_id.get(cds_id) or {}
            seq = rec.get("translation") or ""
            if not seq:
                continue
            key = self._key(cds_id, seq)
            path = self._artifact(cds_id, seq)
            # RETRIEVED already: the bytes are on disk, so nothing is asked again.
            if path.exists():
                self.reused += 1
                out[cds_id] = path
                continue
            try:
                job = self._job_for(key, cds_id, seq)
                if job is None:
                    continue
                body = self._await(key, job, max_polls, poll_seconds)
                if body is None:
                    continue
                # Written before the state says RETRIEVED: a checkpoint that outran the
                # file would send the next run to parse something that is not there.
                path.write_text(body, encoding="utf-8")
                has = self._has_matches(body)
                self.state.completed(key, has, raw_response_path=str(path))
                self.retrieved += 1
                out[cds_id] = path
            except PollBudgetExhausted as exc:
                # Left SUBMITTED/RUNNING with its job id, so the next run polls it.
                self.pending += 1
                self.limitations.append(
                    "InterPro job for %s is still running: %s" % (cds_id, str(exc)[:140]))
            except InterProUnavailable as exc:
                self.failed += 1
                self.state.failed(key, exc.__class__.__name__)
                self._limit(cds_id, str(exc)[:160])
        return out

    def _job_for(self, key, cds_id, seq):
        """A job id, submitting only if this search does not already have one."""
        # `state_of` returns the state string, not the entry -- the entry is where the
        # job id lives. `resumable()` is the state file's own answer to "which searches
        # have a live job id", so the resume rule is read from one definition rather
        # than reconstructed here.
        entry = self.state.resumable().get(key)
        if entry and entry.get("job_id"):
            return entry["job_id"]          # resume: poll, never resubmit
        self.state.declare(key, cds_id, "interpro", sstate.sequence_sha256(seq))
        job = self.transport(seq)
        # Immediately. This is the irreversible step.
        self.state.submitted(key, job)
        self.submitted += 1
        return job

    def _await(self, key, job, max_polls, poll_seconds):
        for _i in range(max_polls):
            status = self.transport.poll_once(job)
            if status == "FINISHED":
                return self.transport.fetch_result(job)
            if status in ("ERROR", "FAILURE", "NOT_FOUND"):
                raise InterProUnavailable(
                    "EBI reported job %s as %s" % (job, status),
                    capability="interproscan")
            self.state.running(key)
            self.sleep(poll_seconds)
        raise PollBudgetExhausted(
            "job %s did not finish within %d polls; it is checkpointed and the next run "
            "will resume polling rather than resubmit" % (job, max_polls))

    @staticmethod
    def _has_matches(body):
        """Whether the response contains any match at all.

        This is the ONE thing read out of the response, and it is a count, not a reading:
        the checkpoint has to distinguish `searched, matches present` from `searched, no
        match`, which is the empty-vs-absent invariant. Nothing about what the matches
        MEAN is decided here or anywhere else in this module.
        """
        try:
            d = json.loads(body)
        except ValueError:
            return False
        for r in d.get("results", d if isinstance(d, list) else []):
            if r.get("matches"):
                return True
        return False

    def _limit(self, cds_id, text):
        self.limitations.append("InterPro submission for %s did not complete: %s"
                                % (cds_id, text))

    def provenance(self):
        """What happened, for the plan and the capability matrix to serialise."""
        attempted = self.submitted + self.reused
        return {
            "provider": "interproscan",
            "mode": "AUTOMATED_REMOTE",
            "endpoint": self.rules.get(_BASE + ".endpoint"),
            "submitted": self.submitted,
            "successful": self.retrieved + self.reused,
            "failed": self.failed,
            "reused_from_prior_state": self.reused,
            # Still running on the service, with a live job id. Not a failure, and not a
            # success: a third state, so the report can say "come back" rather than
            # either "done" or "broken".
            "still_running": self.pending,
            "attempted": attempted,
            # Same shape the remote Stage 2 arm reports, so the capability matrix and the
            # plan read one vocabulary rather than one per provider.
            "systemic_transport_failure": bool(self.failed and not self.retrieved
                                               and not self.reused
                                               and not self.pending),
            "limitations": list(self.limitations),
            "interprets_results": False,
        }
