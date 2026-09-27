"""Remote Stage 2: search UniProtKB through EBI instead of a local BLAST database.

The point is product weight, not science. A user analysing three phages should not have
to download a 176 MB DB_PHAGE plus a host database per genus. The scientific contract is
untouched: PHAGE and HOST are searched separately, merged at hit level, and still yield
exactly ONE S EvidenceRecord.

**The network lives outside Stage 6.** The audit's only job is to say which CDS should be
reopened; it never submits anything. This orchestrator runs at the START of each round,
searching whatever the current pool has not been searched for yet. Stage 6 therefore
stays pure and offline-replayable, and the change radius stays small.

Two orthogonal states, because one cannot express what happened:

    evidence state    searched_hits | searched_no_hit | not_run | not_applicable
    attempt state     never_attempted | attempted_success | attempted_failed

A failed submission leaves evidence at `not_run` -- correctly, since nothing was
learned -- while the attempt state remembers that we already tried. Without that second
axis, the next round would resubmit the same failing job forever, because `not_run` is
exactly what an unsearched candidate looks like.
"""
import hashlib
import json
import pathlib
import time

from .errors import CapabilityUnavailable, InputValidationError
from . import events as events_mod
from . import search_state as sstate
from .parse import blast_tabular, ebi_blast
from .providers import local_files

_BASE = "remote_stage2"

NEVER, SUCCESS, FAILED = "never_attempted", "attempted_success", "attempted_failed"

# Poll outcomes. Three states, because "not finished" and "failed" are different
# answers and a caller that cannot tell them apart will either spin forever or
# treat a live job as dead.
RUNNING, FINISHED, ERROR = "running", "finished", "error"
PHAGE_TAXON = "2731619"          # Caudoviricetes, the same scope as the frozen DB_PHAGE


class RemoteUnavailable(CapabilityUnavailable):
    """The provider could not answer. Not a defect -- a recorded gap in coverage."""


# One condition, one message. The session refuses first (before any sequence is even
# prepared); the transport keeps its own check so a caller using the API directly cannot
# route around it. Two wordings for the same refusal would be a documentation bug.
EMAIL_REQUIRED = (
    "remote Stage 2 needs a contact address for the EMBL-EBI Job Dispatcher. "
    "Pass --ebi-email (or set RBPFINDER_EBI_EMAIL). It is contact information for the "
    "submitted job -- not an RBPfinder account, and not where results are delivered; "
    "RBPfinder retrieves and parses them itself. No sequence has been sent.")


def _sha(seq):
    return hashlib.sha256(seq.encode("utf-8")).hexdigest()


class RemoteStage2Session(object):
    def __init__(self, rules, artifact_dir, submit, policy=None, host_context=None,
                 host_taxid=None, endpoint="https://www.ebi.ac.uk/Tools/services/rest/ncbiblast",
                 email=None, consent=None):
        self.rules = rules
        self.dir = pathlib.Path(artifact_dir)
        self.submit = submit                     # injectable transport: (seq, taxonomy) -> payload
        self.policy = policy
        self.endpoint = endpoint
        self.host_context = host_context
        self.host_taxid = host_taxid
        self.round = 0
        self.reused = []
        self.required_searches = 0
        self.round_limit = rules.get(_BASE + ".remote_round_limit")
        self.email = email
        # Display only. Recovery gets its own persistent state in M19-d; reading this
        # back to skip work would let a truncated log silently drop a real search.
        self.events = None
        self.on_progress = None
        # The recovery source. Distinct from the event log on purpose: this one is
        # rewritten atomically on every transition and is the only thing consulted to
        # decide whether a search may be skipped or resumed.
        self.state = sstate.SearchState(pathlib.Path(artifact_dir) / "search_state.json")
        self.post_count = 0
        self.poll_seconds = 3
        self.poll_timeout = 600
        self.consent = consent                   # () -> bool, asked once, before any send
        self.disclosure = None                   # what the user was told, for provenance
        self.attempts = {}                       # (cds_id, taxonomy, sha) -> state
        self.jobs = []                           # provenance, one entry per submission
        self.per_taxonomy = {}                   # cds_id -> {taxonomy: evidence_state}
        self.limitations = []
        self.round_limit_reached = False
        self._gated = False

    # -- policy ------------------------------------------------------------
    def authorise(self):
        """Gate BEFORE the first sequence leaves the machine, not after.

        Discovering half-way through a batch that the run was never authorised would
        mean the check happened after the thing it was meant to prevent.
        """
        if self._gated:
            return

        # 1. Contact address. Refused here rather than at the first HTTP call so the
        #    run stops before any sequence is prepared for transmission.
        if not self.email:
            raise InputValidationError(EMAIL_REQUIRED)

        # 2. Informed consent, and it runs BEFORE the policy check on purpose.
        #    Both refuse the unauthorised run, so the order does not change who is let
        #    through -- it changes what an unauthorised user learns. Policy-first told
        #    them only that a mode forbade something; consent-first shows them what the
        #    submission would consist of and what the email is for, which is precisely
        #    the information needed to decide whether to authorise it.
        #
        #    Distinct from 3 as a question: the policy says the run MAY transmit, this
        #    says the person was told WHAT is transmitted. A user who believes results
        #    arrive by email will wait for a mail that never comes, or redo the search
        #    by hand on the EBI website and try to feed the file back.
        notice = disclosure_notice(self.rules)
        if self.consent is not None and not self.consent(notice):
            raise RemoteUnavailable(
                "remote submission was not authorised. No sequence has been sent.",
                capability="ebi_blastp")

        # 3. Data policy: is this run permitted to transmit at all. Consent cannot
        #    overrule it -- agreeing to something `private` mode forbids is still
        #    forbidden.
        if self.policy is not None:
            ok, why = self.policy.allows("ebi_blastp")
            if not ok:
                raise RemoteUnavailable(
                    "remote Stage 2 requested but the data policy forbids it: %s. "
                    "No sequence has been sent." % why, capability="ebi_blastp")

        self.disclosure = {"shown": notice,
                           "statements": self.rules.get(_BASE + ".disclosure.must_state"),
                           "authorised": True}
        self._gated = True

    # -- taxonomy ----------------------------------------------------------
    def taxonomies(self):
        """Which taxonomy-restricted searches this run may make, and why not the others.

        A host taxonomy is never guessed. `not_applicable` means nothing was left
        undone (no host was stated); an unresolvable host is a different thing and is
        recorded as such.
        """
        out = [("phage", PHAGE_TAXON)]
        if not self.host_context:
            self.host_state = ("not_applicable", "the run states no host")
        elif not self.host_taxid:
            self.host_state = ("not_run",
                               "host %r could not be resolved to a taxonomy id; "
                               "RBPfinder does not guess taxonomy" % self.host_context)
        else:
            self.host_state = None
            out.append(("host", str(self.host_taxid)))
        return out

    # -- the delta ---------------------------------------------------------
    def _pending(self, records):
        """Keys not yet attempted in THIS run.

        Excludes both successes and failures: a provider that has already failed for a
        key, after its own retries, must not be hammered again inside the same run.
        """
        pending = []
        for rec in records:
            seq = rec.get("translation")
            if not seq:
                continue
            sha = _sha(seq)
            for role, taxonomy in self.taxonomies():
                key = (rec["cds_id"], taxonomy, sha)
                if self.attempts.get(key, NEVER) != NEVER:
                    continue
                # Persistent state outranks in-memory state. A trusted completed result
                # from a previous run -- or imported from a manifest -- means this
                # search is done, whatever this process happens to remember.
                skey = sstate.search_key(rec["cds_id"], taxonomy, sha)
                # Two ways a search can already be trustworthy, and recovery does not
                # care which: a checkpoint from a previous run, or a manifest seeded
                # into the cache. `required - trusted = missing` is one computation.
                if (self.state.state_of(skey) in sstate.TRUSTED_COMPLETE
                        or self.state.manifest_trusted(rec["cds_id"], sha)):
                    self.reused.append(skey)
                    continue
                pending.append((rec, role, taxonomy, sha, key))
        return pending

    def ensure_searched(self, records_by_id, pool):
        """Search whatever in `pool` has not been searched yet. Returns new manifest rows.

        Called at the start of every round, including the first. Round 0 covers the
        initial candidate pool; later rounds cover only what Stage 6 reopened -- the
        delta -- so network cost grows with genuinely new candidates and nothing else.
        """
        records = [records_by_id[c] for c in sorted(pool) if c in records_by_id]
        before_reused = len(self.reused)
        pending = self._pending(records)
        # Everything this round needed, whether it had to be searched now or was already
        # trusted. `required` is the denominator the exit policy compares against.
        self.required_searches += len(pending) + (len(self.reused) - before_reused)
        if not pending:
            return {}

        if self.round >= self.round_limit:
            self.round_limit_reached = True
            self._limit("remote_stage2_round_limit_reached",
                        "the remote round limit (%d) was reached; %d candidate(s) were "
                        "left unsearched" % (self.round_limit,
                                             len({p[0]['cds_id'] for p in pending})))
            return {}

        self.authorise()
        self.round += 1
        self.dir.mkdir(parents=True, exist_ok=True)

        if self.events is None:
            self.events = events_mod.EventLog(self.dir / "stage2_events.jsonl",
                                              total=len(pending))
        else:
            self.events.total += len(pending)

        hits_by_cds = {}
        failures = 0
        for rec, role, taxonomy, sha, key in pending:
            cid = rec["cds_id"]
            # ONE identity for this search. The state machine keys on it and the
            # progress log shows a short form of it -- two presentations, never two
            # hashes. Computing them separately meant state was written under one key
            # and looked up under another, so every restart re-submitted finished work.
            state_key = sstate.search_key(cid, taxonomy, sha)
            skey = events_mod.display_key(state_key)
            started = time.time()
            self.events.submitted(skey, taxonomy=taxonomy)
            if self.on_progress:
                self.on_progress(self.events)
            self.state.declare(state_key, cid, taxonomy, sha)
            try:
                payload = self._run_one(state_key, rec["translation"], taxonomy)
                hits = ebi_blast.read(payload)
            except InputValidationError:
                # a malformed or coordinate-free response is an input defect, not a
                # transport hiccup: it must surface, not be recorded as "no hits"
                raise
            except Exception as exc:
                self.events.failed(skey, type(exc).__name__, taxonomy=taxonomy)
                if self.on_progress:
                    self.on_progress(self.events)
                self.attempts[key] = FAILED
                # FAILED, never searched_no_hit. The two are different answers: one is
                # about the network, the other is about the protein.
                self.state.failed(state_key, type(exc).__name__,
                                  failure_kind=sstate.RETRYABLE)
                failures += 1
                self.per_taxonomy.setdefault(cid, {})[taxonomy] = "not_run"
                self.jobs.append({"cds_id": cid, "taxonomy": taxonomy, "role": role,
                                  "query_sha256": sha, "state": FAILED,
                                  "error": str(exc)[:200], "endpoint": self.endpoint})
                continue

            self.attempts[key] = SUCCESS
            self.events.completed(
                skey,
                events_mod.SEARCHED_HITS if hits else events_mod.SEARCHED_NO_HIT,
                taxonomy=taxonomy)
            if self.on_progress:
                self.on_progress(self.events)
            raw = self.dir / ("%s.%s.raw.json" % (cid, taxonomy))
            raw.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
            self.state.completed(state_key, bool(hits), raw_response_path=raw,
                                 database_release=ebi_blast.database_release(payload))
            self.per_taxonomy.setdefault(cid, {})[taxonomy] = (
                "searched_hits" if hits else "searched_no_hit")
            self.jobs.append({
                "cds_id": cid, "taxonomy": taxonomy, "role": role, "state": SUCCESS,
                "provider": "ebi_job_dispatcher", "endpoint": self.endpoint,
                "database": payload.get("dbs", [{}])[0].get("name", "uniprotkb")
                if payload.get("dbs") else "uniprotkb",
                "query_sha256": sha, "job_id": payload.get("job_id") or payload.get("id"),
                "submitted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "raw_response_path": str(raw),
                # never blank: an empty release reads as "not recorded", then as
                # "not searched"
                "database_release": ebi_blast.database_release(payload),
            })
            hits_by_cds.setdefault(cid, []).extend(hits)

        # Counted from self.jobs, which is the same list provenance reports from --
        # never from a per-round local, or the two disagree the moment a later round
        # fails.
        run_failures = sum(1 for j in self.jobs if j["state"] == FAILED)
        if run_failures:
            self._limit("remote_stage2_incomplete_for_reopened_candidates",
                        "%d remote search(es) did not complete; those candidates remain "
                        "in the pool with no sequence evidence" % run_failures)

        # One S unit per CDS regardless of how many taxonomies answered -- the merge
        # happens here, before any record is built, exactly as the local route does.
        rows = {}
        for cid, hits in hits_by_cds.items():
            merged = local_files._merge_sequence_hits(hits)
            path = self.dir / (cid + ".tsv")
            # an empty file is written on purpose: searched-and-found-nothing is a
            # state on disk, distinct from never-searched
            blast_tabular.write(path, {cid: merged})
            rows[cid] = str(path)
        # CDS that were searched but produced nothing still get their empty artefact
        for rec, role, taxonomy, sha, key in pending:
            cid = rec["cds_id"]
            if cid not in rows and self.attempts.get((cid, taxonomy, sha)) == SUCCESS:
                path = self.dir / (cid + ".tsv")
                blast_tabular.write(path, {cid: []})
                rows[cid] = str(path)
        return rows

    def _run_one(self, skey, sequence, taxonomy):
        """Submit-or-resume one search, checkpointing before anything can be lost.

        The order matters more than the code: the job id is persisted the instant the
        POST returns, BEFORE the first poll. A process killed during polling therefore
        leaves a resumable record, instead of an orphaned job on the service and a
        resubmission here.

        Falls back to the one-call wrapper when the transport exposes no primitives --
        every stub in the test suite is such a transport, and they should not have to
        grow a lifecycle in order to test orchestration.
        """
        prior = self.state.entries.get(skey) or {}
        job_id = prior.get("job_id") if prior.get("state") in sstate.RESUMABLE else None

        submit_only = getattr(self.submit, "submit_only", None)
        poll_once = getattr(self.submit, "poll_once", None)
        fetch_result = getattr(self.submit, "fetch_result", None)
        if not (submit_only and poll_once and fetch_result):
            if job_id:
                # A stub cannot resume. Say so rather than silently reposting.
                self._limit("resume_unsupported_by_transport",
                            "the configured transport exposes no poll/fetch, so job "
                            "%s could not be resumed" % job_id)
            self.post_count += 1
            return self.submit(sequence, taxonomy)

        if job_id is None:
            job_id = submit_only(sequence, taxonomy)
            self.post_count += 1
            self.state.submitted(skey, job_id)      # persisted before the first poll
        # else: a live job from an earlier process. Poll it. Do NOT post again.

        self.state.running(skey)
        waited = 0
        while waited < self.poll_timeout:
            status = poll_once(job_id)
            if status == FINISHED:
                return fetch_result(job_id)
            if status == ERROR:
                raise RemoteUnavailable("EBI job %s ended in an error state" % job_id,
                                        capability="ebi_blastp")
            time.sleep(self.poll_seconds)
            waited += self.poll_seconds
            if self.events:
                self.events.running(skey, waited)
                if self.on_progress:
                    self.on_progress(self.events)
        raise RemoteUnavailable("EBI job %s did not finish within %ds"
                                % (job_id, self.poll_timeout),
                                capability="ebi_blastp")

    def effective_completed(self):
        """Completed searches available to this analysis, from every trusted source.

        Read from the persistent state, so reused-from-cache, newly searched_hits and
        newly searched_no_hit are all counted the same way and by the same code that the
        report and the exit code read.
        """
        summary = self.state.summary()
        return summary["searched_hits"] + summary["searched_no_hit"]

    def _limit(self, lid, text):
        """Deduplicate by id, but keep the TEXT current.

        The first version only appended when the id was new, so a count computed in
        round 1 was frozen there. In the field this produced `failed = 40` in
        run_result.json beside "39 remote search(es) did not complete" in the report --
        the 40th failure happened in a later round and could never update the sentence.
        Two numbers for one fact is a reporting defect, whichever one is right.
        """
        for lim in self.limitations:
            if lim["id"] == lid:
                lim["text"] = text
                return
        self.limitations.append({"id": lid, "text": text})

    def provenance(self):
        host_state = getattr(self, "host_state", None)
        return {
            "backend": "ebi",
            "endpoint": self.endpoint,
            "rounds": self.round,
            "round_limit": self.round_limit,
            "round_limit_reached": self.round_limit_reached,
            "submissions": len(self.jobs),
            "successful": sum(1 for j in self.jobs if j["state"] == SUCCESS),
            "failed": sum(1 for j in self.jobs if j["state"] == FAILED),
            # Systemic transport failure is NOT "no hits". Every search was attempted
            # and none completed, so the honest reading is not_run, and a run that
            # prints a plain OK invites the opposite reading.
            "systemic_transport_failure": bool(
                self.jobs
                and not any(j["state"] == SUCCESS for j in self.jobs)),
            "host_taxonomy": ({"state": host_state[0], "detail": host_state[1]}
                              if host_state else
                              {"state": "searched", "taxid": str(self.host_taxid)}),
            # What the user was told before the first sequence left, kept with the run
            # rather than only on their screen: a consent that cannot be reconstructed
            # afterwards is not auditable.
            "disclosure": self.disclosure,
            # The compact state an agent should read INSTEAD of the raw responses.
            "progress": self.events.summary() if self.events else None,
            # Recovery facts, kept apart from display facts.
            "search_state_path": str(self.state.path),
            "search_state": self.state.summary(),
            "reused_from_prior_state": len(self.reused),
            "post_count": self.post_count,
            # What Stage 2 achieved for this ANALYSIS, which is not what this process
            # happened to submit. A run that reuses 39 cached results and fails its one
            # new search has not failed Stage 2; counting only new successes says it did.
            #
            # searched_no_hit counts as completed. Not a hit, but a finished search --
            # the same distinction this project defends everywhere else.
            "effective_completed": self.effective_completed(),
            "required_searches": self.required_searches,
            "events_path": str(self.dir / "stage2_events.jsonl"),
            "events_are_recovery_source": events_mod.IS_RECOVERY_SOURCE,
            "per_taxonomy_state": self.per_taxonomy,
            "jobs": self.jobs,
            "limitations": self.limitations,
        }


def disclosure_notice(rules):
    """The exact text the user must see before the first sequence leaves the machine.

    Read from YAML rather than written here, so the wording the tests assert on and the
    wording the user reads cannot drift apart.
    """
    return rules.get(_BASE + ".disclosure.notice")


def disclosure_prompt(rules):
    return rules.get(_BASE + ".disclosure.prompt")


def http_submit(endpoint, poll_seconds=3, timeout=600, attempts=5,
                backoff=(1, 2, 4, 8), email=None, on_raw=None):
    """The real EBI Job Dispatcher transport: submit, poll, fetch JSON.

    Returns a callable with the same narrow signature the orchestrator expects, so the
    tests can substitute a stub without the orchestrator knowing the difference. All of
    M16-b's evidence comes from stubs; this function is exercised for the first time in
    M16-d, and until then nothing that depends on it may be called network-validated.

    Retry lives HERE, inside the transport, exactly as the acquisition fetchers do. A
    transport hiccup must never travel up and become a different backend choice, and it
    must never be recorded as "the provider had nothing" -- this machine drops 20-50% of
    outbound TLS connections on every host, and one job is at least three round trips.

    It retries transport failures only. An HTTP 4xx (other than 429) or a job the
    service reports as ERROR is an answer, and is raised immediately.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    if not email:
        raise InputValidationError(EMAIL_REQUIRED)

    def _retry(what, call):
        """Retry transport failures, never answers.

        A dropped connection says nothing about the query, so it is worth asking again.
        A 400, a 404 or a job in state ERROR is an answer -- retrying it would turn a
        real result into a hang. The split is the whole point; see YAML
        `remote_stage2.transport_retry`.
        """
        last = None
        for i in range(attempts):
            try:
                return call()
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise RemoteUnavailable(
                        "EBI %s returned HTTP %d, which is an answer rather than a "
                        "transport failure; not retrying" % (what, exc.code),
                        capability="ebi_blastp")
                last = exc
            except (urllib.error.URLError, OSError) as exc:
                last = exc
            if i < attempts - 1:
                time.sleep(backoff[min(i, len(backoff) - 1)])
        raise RemoteUnavailable(
            "EBI %s failed %d times in a row; last error: %s" % (what, attempts, last),
            capability="ebi_blastp")

    def _post(path, data):
        def once():
            req = urllib.request.Request(
                endpoint + path, data=urllib.parse.urlencode(data).encode("utf-8"),
                headers={"User-Agent": "RBPfinder", "Accept": "text/plain"})
            with urllib.request.urlopen(req, timeout=60) as fh:
                return fh.read().decode("utf-8").strip()
        return _retry("POST " + path, once)

    def _get(path, accept="application/json"):
        def once():
            req = urllib.request.Request(
                endpoint + path,
                headers={"User-Agent": "RBPfinder", "Accept": accept})
            with urllib.request.urlopen(req, timeout=60) as fh:
                return fh.read().decode("utf-8")
        return _retry("GET " + path, once)

    # ---- the three primitives -------------------------------------------
    # Split in M19-d. As one call, a process killed during polling left a live EBI job
    # with no locally recoverable job_id, so restarting could only POST again -- the
    # single behaviour the field report most wanted gone. Separated, `SUBMITTED(job_id)`
    # becomes a checkpointable state and recovery can poll instead of resubmit.
    def submit_only(sequence, taxonomy):
        """POST the job. Returns a job id and nothing else. This is the ONLY step that
        creates work on the service, so it is the only one that must never be repeated
        for a search that already has an id."""
        return _post("/run", {
            "email": email, "program": "blastp", "stype": "protein",
            "database": "uniprotkb", "sequence": sequence,
            # taxonomy restriction is what makes PHAGE and HOST two different searches
            # against one service rather than two databases to download
            "taxids": str(taxonomy),
        })

    def poll_once(job_id):
        """One status check. Returns RUNNING / FINISHED / ERROR -- never blocks."""
        # The job id comes from the service, so it is not ours to trust into a URL
        # unencoded -- same rule as any other value entering a URL.
        status = _get("/status/" + urllib.parse.quote(job_id, safe=""),
                      accept="text/plain").strip()
        if status == "FINISHED":
            return FINISHED
        if status in ("ERROR", "FAILURE", "NOT_FOUND"):
            return ERROR
        return RUNNING

    def fetch_result(job_id):
        """GET the finished result. Idempotent, and safe to repeat after a restart."""
        text = _get("/result/%s/json" % urllib.parse.quote(job_id, safe=""))
        # The bytes the service actually sent, before anything reinterprets them.
        # `json.dumps(payload)` is a re-serialisation, not a recording: it sorts keys,
        # re-indents, and silently normalises numbers. M16-d needs the original.
        if on_raw is not None:
            on_raw(job_id, text)
        payload = json.loads(text)
        payload.setdefault("job_id", job_id)
        return payload

    def wait_until_done(job_id):
        waited = 0
        while waited < timeout:
            state = poll_once(job_id)
            if state == FINISHED:
                return
            if state == ERROR:
                raise RemoteUnavailable("EBI job %s ended in an error state" % job_id,
                                        capability="ebi_blastp")
            time.sleep(poll_seconds)
            waited += poll_seconds
        raise RemoteUnavailable("EBI job %s did not finish within %ds"
                                % (job_id, timeout), capability="ebi_blastp")

    def submit(sequence, taxonomy):
        """Compatibility wrapper: the old one-call shape, composed from the primitives.

        Kept so every existing caller and the M16 regressions keep working unchanged.
        The resume runtime does NOT use this -- it drives the three primitives itself,
        because a wrapper that blocks until finished cannot be interrupted and resumed.
        """
        job_id = submit_only(sequence, taxonomy)
        wait_until_done(job_id)
        return fetch_result(job_id)

    submit.submit_only = submit_only
    submit.poll_once = poll_once
    submit.fetch_result = fetch_result
    submit.wait_until_done = wait_until_done
    return submit
