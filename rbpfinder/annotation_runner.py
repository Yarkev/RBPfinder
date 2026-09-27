"""EXPERIMENTAL -- developer-only. NOT on the default product path.

RBPfinder does not annotate genomes. M19-f was deliberately re-scoped: owning a remote
annotation provider would mean owning third-party accounts, terms of service, rate
limits, upload lifecycles, provider version drift and data-retention disclosure, none of
which is this tool's competence, and all of which would sit between a user and the RBP
work. `--fasta` now prints guidance and transmits nothing.

This module is kept because the lifecycle discipline in it -- one canonical identity,
checkpoint before every irreversible step, non-blocking polling, strict artefact
validation, credential containment -- is reusable infrastructure and was expensive to get
right. It is not reachable from the CLI.

M19-f2b..f2e -- drive the Galaxy annotation lifecycle, checkpointing as it goes.

The ordering rule is the whole module: **the identifier is persisted before the step that
produced it can be lost.** A history that exists on someone else's server but not in our
checkpoint is litter we will never clean up, and a re-run that creates a second one is the
EBI resubmission problem wearing different clothes.

    create_history  -> checkpoint history_id
    upload_fasta    -> checkpoint dataset_id      <- the genome leaves here, once
    run_pharokka    -> checkpoint job_id
    poll_once       -> (no state to lose)
    fetch_genbank   -> validate, then checkpoint completed

Validation is deliberately strict about *shape* and deliberately silent about *content*.
Whether this provider calls the same number of CDS as a local Pharokka is an agreement
question for f4; whether it returned something the executor can read at all is a transport
question, and that is what belongs here.
"""
import json
import pathlib

from .errors import CapabilityUnavailable, InputValidationError
from . import annotation_state as astate

# What a returned file must look like before anything downstream touches it.
_GENBANK_MARKERS = ("LOCUS", "FEATURES", "ORIGIN")


def validate_genbank(path, min_cds=1):
    """Refuse anything that is not a usable annotated GenBank.

    The hostile case this exists for: a service returns HTTP 200 with an HTML error page,
    it gets written to `something.gbk`, and the extension makes it look ingestible. A
    file is what it contains, never what it is called.

    Returns a small summary. Raises rather than returning a verdict flag, because a
    caller that has to remember to check a flag is a caller that will forget.
    """
    path = pathlib.Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise CapabilityUnavailable(
            "the annotation provider returned an empty file; an empty GenBank is not "
            "an annotation with no genes, it is a failed retrieval",
            capability="genome_annotation")
    text = path.read_text(encoding="utf-8", errors="replace")
    head = text.lstrip()[:400].lower()
    if head.startswith("<") or "<html" in head or "<!doctype" in head:
        raise CapabilityUnavailable(
            "the provider returned an HTML page, not a GenBank. A file named .gbk that "
            "contains an error page must never reach the ingest.",
            capability="genome_annotation")
    if head.startswith("{") or head.startswith("["):
        raise CapabilityUnavailable(
            "the provider returned JSON, not a GenBank -- most likely an error payload "
            "delivered with HTTP 200.", capability="genome_annotation")
    missing = [m for m in _GENBANK_MARKERS if m not in text]
    if missing:
        raise CapabilityUnavailable(
            "the returned file is not a parseable GenBank (missing %s)"
            % ", ".join(missing), capability="genome_annotation")

    # Parsed, not pattern-matched: the executor will parse it, so this must too.
    try:
        from Bio import SeqIO
        records = list(SeqIO.parse(str(path), "genbank"))
    except Exception as exc:                                   # noqa: BLE001
        raise CapabilityUnavailable(
            "the returned GenBank could not be parsed: %s" % str(exc)[:160],
            capability="genome_annotation")
    if not records:
        raise CapabilityUnavailable("the returned GenBank holds no records",
                                    capability="genome_annotation")

    cds, translated, bad_coords = 0, 0, []
    for rec in records:
        length = len(rec.seq) if rec.seq is not None else 0
        for feat in rec.features:
            if feat.type != "CDS":
                continue
            cds += 1
            tr = (feat.qualifiers.get("translation") or [""])[0]
            if tr.strip():
                translated += 1
            start = int(feat.location.start)
            end = int(feat.location.end)
            if not (0 <= start < end <= max(length, end)):
                bad_coords.append("%d..%d" % (start, end))
    if cds < min_cds:
        raise CapabilityUnavailable(
            "the annotation contains %d CDS features; this is a failed annotation, not "
            "a genome without proteins" % cds, capability="genome_annotation")
    if not translated:
        raise CapabilityUnavailable(
            "no CDS in the returned annotation carries a translation; without protein "
            "sequences there is nothing for the executor to search",
            capability="genome_annotation")
    if bad_coords:
        raise CapabilityUnavailable(
            "the annotation carries impossible coordinates (%s)"
            % "; ".join(bad_coords[:3]), capability="genome_annotation")
    return {"records": len(records), "cds": cds, "cds_with_translation": translated}


class GalaxyAnnotationRunner(object):
    """Resumable driver. Every remote id reaches disk before the next call is made."""

    def __init__(self, provider, state, tos_confirmed=False):
        self.provider = provider
        self.state = state
        self.tos_confirmed = tos_confirmed
        self.calls = {"create_history": 0, "upload": 0, "run": 0,
                      "poll": 0, "fetch": 0}

    def identity(self, fasta_path, params=None):
        cfg = astate.config_sha256(params or {}, self.provider.tool_id,
                                   self.provider.tool_version)
        sha = astate.file_sha256(fasta_path)
        key = astate.annotation_identity(self.provider.provider_id, sha,
                                         self.provider.tool_id, cfg)
        return key, sha, cfg

    def start(self, fasta_path, phage_id, params=None, out_dir=None):
        """Advance the lifecycle by whatever steps are still missing.

        Safe to call repeatedly: each step is skipped when the checkpoint already holds
        its identifier. That is what makes an interrupted annotation resumable instead of
        a second history on the server.
        """
        key, sha, cfg = self.identity(fasta_path, params)
        reusable, why = self.state.reusable(key, sha, cfg)
        if reusable:
            return key, self.state.entry(key)

        # No genome moves until a person has read the terms. Checked here, before the
        # first call that could transmit, not at the end.
        self.provider.tos_gate_satisfied(self.tos_confirmed)

        self.state.declare(key, self.provider.provider_id, sha,
                           self.provider.tool_id, cfg)
        entry = self.state.entry(key)

        if not entry.get("history_id"):
            hid = self.provider.create_history("rbpfinder-%s" % phage_id)
            self.calls["create_history"] += 1
            self.state.history_created(key, hid)      # before the upload can be lost
            entry = self.state.entry(key)

        if not entry.get("dataset_id"):
            did = self.provider.upload_fasta(entry["history_id"], fasta_path,
                                             name="%s.fasta" % phage_id)
            self.calls["upload"] += 1
            self.state.uploaded(key, did)
            entry = self.state.entry(key)

        if not entry.get("job_id"):
            job_id, _outputs = self.provider.run_pharokka(
                entry["history_id"], entry["dataset_id"], params)
            self.calls["run"] += 1
            self.state.submitted(key, job_id)
            entry = self.state.entry(key)

        return key, entry

    def poll(self, key):
        entry = self.state.entry(key)
        job_id = entry.get("job_id")
        if not job_id:
            raise InputValidationError(
                "no job id checkpointed for this annotation; call start() first")
        status = self.provider.poll_once(job_id)
        self.calls["poll"] += 1
        if status == "running" and entry.get("state") != astate.RUNNING:
            self.state.running(key)
        return status

    def retrieve(self, key, dest_path):
        """Download, VALIDATE, then record completion. Never the other way round."""
        entry = self.state.entry(key)
        path = self.provider.fetch_genbank(entry["job_id"], dest_path)
        self.calls["fetch"] += 1
        # A file that fails validation must not be recorded as a completed annotation:
        # the next run would reuse it and the failure would become permanent.
        summary = validate_genbank(path)
        self.state.completed(key, path, tool_version=self.provider.tool_version)
        return path, summary


class GalaxyAnnotationProvider(object):
    """Adapts the Galaxy lifecycle to the `AnnotationSession` provider interface.

    `AnnotationSession` stays the only entry point. This class owns the *waiting* --
    `poll_once` still cannot block, and deciding when to look again is a runtime job, not
    a transport one. Putting the loop here rather than in the provider is what keeps an
    interrupted annotation resumable.
    """

    transmits = True

    def __init__(self, inner, state, tos_confirmed=False, params=None,
                 poll_seconds=10, timeout=1800, on_progress=None):
        self.inner = inner
        self.provider_id = inner.provider_id
        self.runner = GalaxyAnnotationRunner(inner, state,
                                             tos_confirmed=tos_confirmed)
        self.params = params or {}
        self.poll_seconds = poll_seconds
        self.timeout = timeout
        self.on_progress = on_progress
        self.identity_key = None

    def annotate(self, genome_fasta, phage_id, out_dir):
        """Runs the lifecycle with the credential contained at this boundary.

        Every exception raised anywhere inside is re-raised with the key removed. The
        redaction inside the transport is not enough on its own: a library, a callback
        or a careless upstream error message can carry the key out through a path that
        never touched `_request`, and the CLI prints a traceback for unexpected errors.
        Found by the destructive gate, not by reading the code.
        """
        try:
            return self._annotate(genome_fasta, phage_id, out_dir)
        except BaseException as exc:
            from .providers.galaxy import redact
            cleaned = redact(str(exc), getattr(self.inner, "_key", None))
            if cleaned != str(exc):
                # Rebuild rather than mutate: the original traceback would still hold
                # the frame that formatted the key into the message.
                raise type(exc)(cleaned) from None
            raise

    def _annotate(self, genome_fasta, phage_id, out_dir):
        import time
        key, entry = self.runner.start(genome_fasta, phage_id, self.params,
                                       out_dir=out_dir)
        self.identity_key = key

        dest = pathlib.Path(out_dir) / ("%s.annotated.gbk" % phage_id)
        if entry.get("state") == astate.COMPLETED and entry.get(
                "annotated_genbank_path"):
            # Already done under this exact identity. Nothing is re-fetched and nothing
            # is re-submitted.
            dest = pathlib.Path(entry["annotated_genbank_path"])
        else:
            waited = 0
            while waited < self.timeout:
                status = self.runner.poll(key)
                if status == "finished":
                    break
                if status == "error":
                    self.runner.state.failed(key, "the Galaxy job ended in an error "
                                                  "state")
                    raise CapabilityUnavailable(
                        "the Galaxy annotation job failed; no GenBank was produced",
                        capability="genome_annotation")
                if self.on_progress:
                    self.on_progress(waited, self.timeout)
                time.sleep(self.poll_seconds)
                waited += self.poll_seconds
            else:
                raise CapabilityUnavailable(
                    "the Galaxy annotation did not finish within %ds. The job id is "
                    "checkpointed, so re-running resumes it rather than submitting "
                    "again." % self.timeout, capability="genome_annotation")
            dest, _summary = self.runner.retrieve(key, dest)

        entry = self.runner.state.entry(key)
        return {
            "annotated_genbank": dest,
            # Optional enrichment the Galaxy Pharokka tool does not return as separate
            # artefacts. Absent, recorded, and never evidence against anything.
            "phold": None,
            "foldseek": None,
            "provenance": dict(self.inner.provenance(), **{
                "annotation_identity": key,
                "annotation_config_sha256": entry.get("annotation_config_sha256"),
                "input_fasta_sha256": entry.get("input_fasta_sha256"),
                "history_id": entry.get("history_id"),
                "dataset_id": entry.get("dataset_id"),
                "job_id": entry.get("job_id"),
                "tool_versions": {self.inner.cfg["tool"]: self.inner.tool_version},
            }),
        }


class PASAnnotationProvider(object):
    """Adapts the Phage Annotation Server to the `AnnotationSession` interface.

    Simpler than the Galaxy adapter because the service is: one POST creates the job, and
    there is no history or dataset to track. The lifecycle discipline is unchanged --
    the job id is checkpointed the instant the POST returns, and the waiting happens
    here rather than in the transport.
    """

    transmits = True

    def __init__(self, inner, state, params=None, on_progress=None, email=None):
        self.inner = inner
        self.provider_id = inner.provider_id
        self.state = state
        self.params = params or {}
        self.on_progress = on_progress
        self.email = email                      # optional; omitted unless asked for
        self.identity_key = None
        self.calls = {"submit": 0, "poll": 0, "fetch": 0}

    def _identity(self, fasta_path):
        cfg = astate.config_sha256(self.params, self.inner.provider_id,
                                   json.dumps(self.inner.tool_versions, sort_keys=True))
        sha = astate.file_sha256(fasta_path)
        key = astate.annotation_identity(self.inner.provider_id, sha,
                                         self.inner.provider_id, cfg)
        return key, sha, cfg

    def annotate(self, genome_fasta, phage_id, out_dir):
        key, sha, cfg = self._identity(genome_fasta)
        self.identity_key = key
        reusable, _why = self.state.reusable(key, sha, cfg)
        entry = self.state.entry(key)
        dest = pathlib.Path(out_dir) / ("%s.annotated.gbk" % phage_id)

        if reusable and entry.get("annotated_genbank_path"):
            dest = pathlib.Path(entry["annotated_genbank_path"])
        else:
            # An expired job is NOT a reason to start a new one. The results lived 30
            # days and are gone; resubmitting would transmit the whole genome again
            # without anyone asking. That has to be a deliberate act.
            if entry.get("state") == astate.FAILED and entry.get("failure_reason"):
                pass
            if entry.get("expired"):
                raise CapabilityUnavailable(
                    "the previous annotation job expired (results are kept for 30 "
                    "days). RBPfinder does not resubmit automatically, because that "
                    "would transmit the complete genome again. Re-run with "
                    "--restart-annotation to submit a new job deliberately.",
                    capability="genome_annotation")

            self.state.declare(key, self.inner.provider_id, sha,
                               self.inner.provider_id, cfg)
            job_id = entry.get("job_id")
            if not job_id:
                job_id = self.inner.submit_job(genome_fasta, self.params, self.email)
                self.calls["submit"] += 1
                self.state.submitted(key, job_id)     # before the first poll
            dest = self._await_and_fetch(key, job_id, dest)

        entry = self.state.entry(key)
        return {
            "annotated_genbank": dest,
            # phold and phynteny ran INSIDE the same job. Recorded as provider pipeline
            # stages, deliberately NOT as an RBPfinder Foldseek or HHpred execution.
            "phold": None,
            "foldseek": None,
            "provenance": dict(self.inner.provenance(), **{
                "annotation_identity": key,
                "annotation_config_sha256": entry.get("annotation_config_sha256"),
                "input_fasta_sha256": entry.get("input_fasta_sha256"),
                "job_id": entry.get("job_id"),
                "tool_versions": dict(self.inner.tool_versions),
            }),
        }

    def _await_and_fetch(self, key, job_id, dest):
        import time
        waited = 0
        while waited < self.inner.poll_timeout:
            status = self.inner.poll_once(job_id)
            self.calls["poll"] += 1
            if status == "finished":
                break
            if status == "expired":
                self.state._set(key, expired=True)
                raise CapabilityUnavailable(
                    "the annotation job has expired on the server; its results are no "
                    "longer retrievable. Re-run with --restart-annotation to submit a "
                    "new job deliberately.", capability="genome_annotation")
            if status == "error":
                detail = self.inner.status_detail(job_id)
                self.state.failed(key, detail.get("error_message") or "job failed")
                raise CapabilityUnavailable(
                    "the annotation job failed: %s%s"
                    % (detail.get("error_message") or detail.get("error_code")
                       or "no reason given",
                       (" -- " + detail["error_hint"]) if detail.get("error_hint")
                       else ""),
                    capability="genome_annotation")
            if self.on_progress:
                self.on_progress(waited, self.inner.poll_timeout)
            time.sleep(self.inner.poll_seconds)
            waited += self.inner.poll_seconds
        else:
            raise CapabilityUnavailable(
                "the annotation did not finish within %ds. The job id is checkpointed, "
                "so re-running resumes polling rather than submitting again."
                % self.inner.poll_timeout, capability="genome_annotation")

        path = self.inner.fetch_genbank(job_id, dest)
        self.calls["fetch"] += 1
        validate_genbank(path)                    # before it counts as completed
        self.state.completed(key, path,
                             tool_version=json.dumps(self.inner.tool_versions,
                                                     sort_keys=True))
        return path
