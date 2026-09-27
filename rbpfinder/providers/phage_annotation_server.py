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

M19-f2 -- Phage Annotation Server as the primary remote annotation provider.

Zero configuration: no account, no API key. The contract below is frozen from the
service's own `/openapi.json` and its official `/api/client.py`, not from prose.

Three decisions worth reading before the code, because each of them is a place where the
obvious implementation is wrong:

**A POST whose outcome is unknown is never retried.** The dangerous case is not a refused
connection; it is a `POST /jobs` that reached the server, created a job, and whose
response was lost on the way back. Retrying that submits the genome a second time and
leaves an orphan job on a shared academic service. `urllib` cannot tell us whether the
request bytes were sent, and the API exposes no idempotency key, so the conservative
reading is the only honest one: submission failures are reported, never retried. One
manual re-run costs less than two silent remote jobs. GETs are idempotent and do retry.

**HTTP 429 is not a transient transport failure.** M15C's bounded retry exists for a
machine that drops 20-50% of its TLS connections. A rate limit is a service telling us to
stop, and the terms ask users not to circumvent per-IP limits -- so retrying it
automatically *is* the circumvention. It fails with guidance instead.

**An expired job is not a reason to start a new one.** Results live 30 days. Recovering
onto an expired job and silently resubmitting would transmit the whole genome again
without anyone asking for it; that has to be a deliberate act.
"""
import json
import mimetypes
import pathlib
import time
import urllib.error
import urllib.request
import uuid

from ..errors import CapabilityUnavailable, InputValidationError

_BASE = "genome_annotation"

QUEUED, RUNNING, FINISHED, ERROR, EXPIRED = (
    "queued", "running", "finished", "error", "expired")

# Terminal states as the service names them, mapped onto the vocabulary the orchestrator
# already speaks. `expired` is kept distinct from `error`: one means the work failed, the
# other means it succeeded and we were too late.
_STATE_MAP = {"completed": FINISHED, "failed": ERROR, "expired": EXPIRED}


class RateLimited(CapabilityUnavailable):
    """HTTP 429. Deliberately not retried anywhere."""


class SubmissionOutcomeUnknown(CapabilityUnavailable):
    """The POST may or may not have created a job. Never retried automatically."""


def validate_input(fasta_path, caps):
    """Check the published caps locally, BEFORE uploading.

    An oversized genome should fail on this machine rather than by consuming someone
    else's share of a rate-limited academic service.
    """
    path = pathlib.Path(fasta_path)
    if not path.exists():
        raise InputValidationError("%s does not exist" % path)
    size = path.stat().st_size
    if size > caps["max_upload_bytes"]:
        raise InputValidationError(
            "%s is %.1f MB; the service accepts at most %.0f MB on the wire"
            % (path.name, size / 1e6, caps["max_upload_bytes"] / 1e6))

    contigs, lengths, current = 0, [], 0
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if line.startswith(">"):
            if contigs:
                lengths.append(current)
            contigs += 1
            current = 0
        else:
            current += len(line.strip())
    if contigs:
        lengths.append(current)

    if not contigs:
        raise InputValidationError("%s contains no FASTA records" % path.name)
    if contigs > caps["max_contigs"]:
        raise InputValidationError(
            "%s has %d contigs; the service accepts at most %d. This is a single-genome "
            "service, not a metagenome pipeline."
            % (path.name, contigs, caps["max_contigs"]))
    total = sum(lengths)
    if total > caps["max_total_bp"]:
        raise InputValidationError(
            "%s totals %d bp; the service accepts at most %d bp"
            % (path.name, total, caps["max_total_bp"]))
    short = [n for n in lengths if n < caps["min_contig_bp"]]
    if short:
        raise InputValidationError(
            "%s contains %d contig(s) shorter than %d bp"
            % (path.name, len(short), caps["min_contig_bp"]))
    long_ones = [n for n in lengths if n > caps["max_contig_bp"]]
    if long_ones:
        raise InputValidationError(
            "%s contains a contig of %d bp; the maximum is %d"
            % (path.name, max(long_ones), caps["max_contig_bp"]))

    # Protein FASTA is rejected upstream anyway; catching it here saves a wasted job.
    if caps.get("nucleotide_only"):
        seq = "".join(l.strip() for l in text.splitlines()
                      if l and not l.startswith(">"))[:4000].upper()
        if seq and sum(c in "ACGTUNRYKMSWBDHV-" for c in seq) / len(seq) < 0.9:
            raise InputValidationError(
                "%s does not look like nucleotide FASTA. The service accepts nucleotide "
                "sequence only and rejects protein input." % path.name)
    return {"contigs": contigs, "total_bp": total}


class PhageAnnotationServerProvider(object):
    """Four primitives, matching the published API one for one."""

    provider_id = "phage_annotation_server"
    transmits = True
    credentials_required = False        # no account, no key -- and no resolver either

    def __init__(self, rules, endpoint=None, opener=None):
        cfg = rules.get(_BASE + ".providers.phage_annotation_server")
        self.rules = rules
        self.cfg = cfg
        self.endpoint = (endpoint or cfg["endpoint"]).rstrip("/")
        self.caps = cfg["input_caps"]
        self.poll_seconds = cfg["poll_seconds"]
        self.poll_timeout = cfg["poll_timeout_seconds"]
        self.output_path = cfg["output"]
        self.tool_versions = dict(cfg["tool_versions"])
        self._opener = opener

    # -- disclosure --------------------------------------------------------
    def version_notice(self):
        return (
            "Remote annotation provider: Phage Annotation Server\n"
            "  pharokka %s -> phold %s -> phynteny %s\n\n"
            "A free academic service. No account and no API key are required, but the\n"
            "COMPLETE genome is still transmitted. Results are kept for 30 days and then\n"
            "deleted. If your sequence is confidential or under an agreement that\n"
            "forbids transmission, use local annotation instead."
            % (self.tool_versions.get("pharokka"), self.tool_versions.get("phold"),
               self.tool_versions.get("phynteny_transformer")))

    def tos_gate_satisfied(self, confirmed=True):
        """No per-operator review is outstanding here: the terms are public, readable,
        and explicitly permit scripted single-genome use. The genome-transmission
        DISCLOSURE is a separate gate and is still enforced by AnnotationSession."""
        return True

    # -- transport ---------------------------------------------------------
    def _open(self, req, timeout=120):
        opener = self._opener or urllib.request.urlopen
        return opener(req, timeout=timeout)

    def _get(self, path, accept="application/json", retries=3):
        """GETs are idempotent, so a dropped connection may be retried.

        This is the M15C policy and it is safe here for the reason M15C's is safe: asking
        again cannot create anything.
        """
        url = self.endpoint + path
        last = None
        for attempt in range(retries):
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", accept)
            req.add_header("User-Agent", "rbpfinder/1.x")
            try:
                with self._open(req) as resp:
                    return resp.status, resp.read(), dict(resp.headers or {})
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    raise RateLimited(self._rate_limit_message(dict(exc.headers or {})),
                                      capability="phage_annotation_server")
                return exc.code, exc.read(), dict(exc.headers or {})
            except Exception as exc:                            # noqa: BLE001
                last = exc
                if attempt < retries - 1:
                    time.sleep((1, 2, 4)[min(attempt, 2)])
        raise CapabilityUnavailable(
            "could not reach %s after %d attempts: %s" % (path, retries, str(last)[:160]),
            capability="phage_annotation_server")

    def _rate_limit_message(self, headers):
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
        wait = (" The service asked us to wait %s seconds." % retry_after
                if retry_after else "")
        return ("the annotation service rate-limited this request (HTTP 429).%s\n"
                "RBPfinder does not retry a rate limit automatically -- the service's "
                "terms ask users not to circumvent the per-IP limits. Wait and run the "
                "same command again; the checkpoint means nothing is lost." % wait)

    # 1 ------------------------------------------------------------------
    def submit_job(self, fasta_path, params=None, email=None):
        """The one irreversible step: this is where the genome leaves the machine.

        Never retried. See the module docstring -- a lost response to a successful POST
        is indistinguishable from a POST that never arrived, and guessing wrong means two
        genome uploads and an orphan job on a shared service.
        """
        path = pathlib.Path(fasta_path)
        validate_input(path, self.caps)          # local caps first; no wasted upload

        fields = {"gene_predictor": (params or {}).get("gene_predictor", "phanotate")}
        if (params or {}).get("fast", True):
            fields["fast"] = "true"
        if email:                                 # optional, and omitted by default
            fields["email"] = email

        body, ctype = _multipart(fields, {"fasta": path})
        req = urllib.request.Request(self.endpoint + "/jobs", data=body, method="POST")
        req.add_header("Accept", "application/json")   # else a 303 browser redirect
        req.add_header("Content-Type", ctype)
        req.add_header("User-Agent", "rbpfinder/1.x")
        try:
            with self._open(req, timeout=300) as resp:
                status, payload = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                # The request was refused, so nothing was created. Still not retried.
                raise RateLimited(self._rate_limit_message(dict(exc.headers or {})),
                                  capability="phage_annotation_server")
            raise CapabilityUnavailable(
                "the annotation service rejected the submission (HTTP %d): %s"
                % (exc.code, _error_of(exc.read())),
                capability="phage_annotation_server")
        except Exception as exc:                            # noqa: BLE001
            raise SubmissionOutcomeUnknown(
                "the connection failed while submitting the genome, and it cannot be "
                "determined whether the job was created (%s). RBPfinder does NOT "
                "resubmit automatically: doing so could upload the genome twice and "
                "leave an orphan job on a shared service. Check "
                "https://phage-annotation.org/my-jobs, then re-run if nothing is there."
                % str(exc)[:120], capability="phage_annotation_server")

        if status not in (200, 202):
            raise CapabilityUnavailable(
                "submission failed (HTTP %d): %s" % (status, _error_of(payload)),
                capability="phage_annotation_server")
        data = json.loads(payload)
        job_id = data.get("job_id")
        if not job_id:
            raise CapabilityUnavailable(
                "the service accepted the submission but returned no job id",
                capability="phage_annotation_server")
        return job_id

    # 2 ------------------------------------------------------------------
    def poll_once(self, job_id):
        """One status check. No loop, no sleep -- cadence belongs to the orchestrator."""
        status, payload, _h = self._get("/jobs/%s/status.json" % job_id)
        if status != 200:
            raise CapabilityUnavailable(
                "status check failed (HTTP %d)" % status,
                capability="phage_annotation_server")
        info = json.loads(payload)
        state = info.get("state")
        self.last_status = info
        return _STATE_MAP.get(state, RUNNING if state in ("queued", "running")
                              else RUNNING)

    def status_detail(self, job_id):
        """The error triple, rendered safely. Kept apart from `poll_once` so the state
        machine has one job."""
        status, payload, _h = self._get("/jobs/%s/status.json" % job_id)
        info = json.loads(payload) if status == 200 else {}
        return {"state": info.get("state"), "stage": info.get("stage"),
                "elapsed_s": info.get("elapsed_s"),
                "error_code": info.get("error_code"),
                "error_message": str(info.get("error_message") or "")[:300],
                "error_hint": str(info.get("error_hint") or "")[:300]}

    # 3 ------------------------------------------------------------------
    def fetch_genbank(self, job_id, dest_path):
        """`phold/phold.gbk` -- the one required artefact."""
        status, payload, _h = self._get(
            "/jobs/%s/files/%s" % (job_id, self.output_path), accept="*/*")
        if status != 200:
            raise CapabilityUnavailable(
                "the annotation job produced no %s (HTTP %d); without a GenBank there "
                "is nothing to analyse" % (self.output_path, status),
                capability="phage_annotation_server")
        dest = pathlib.Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return dest

    # 4 ------------------------------------------------------------------
    def fetch_bundle(self, job_id, dest_path):
        """The whole archive. Optional: its absence never fails an annotation."""
        try:
            status, payload, _h = self._get("/jobs/%s/download" % job_id, accept="*/*")
        except CapabilityUnavailable:
            return None
        if status != 200:
            return None
        dest = pathlib.Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        return dest

    def provenance(self):
        return {
            "provider": self.provider_id,
            "endpoint": self.endpoint,
            "pipeline": list(self.cfg["pipeline"]),
            "tool_versions": dict(self.tool_versions),
            "classification": self.cfg["classification"],
            "zero_configuration": self.cfg["zero_configuration"],
            "retention_days": self.cfg["retention_days"],
            "credentials_required": self.credentials_required,
        }


def _error_of(payload):
    try:
        return str(json.loads(payload).get("error", ""))[:300]
    except Exception:                                           # noqa: BLE001
        return payload[:200].decode("utf-8", "replace") if payload else ""


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    out = []

    def w(text):
        out.append(text.encode("utf-8"))

    for name, value in fields.items():
        w("--%s\r\n" % boundary)
        w('Content-Disposition: form-data; name="%s"\r\n\r\n' % name)
        w("%s\r\n" % value)
    for name, path in files.items():
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        w("--%s\r\n" % boundary)
        w('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
          % (name, path.name))
        w("Content-Type: %s\r\n\r\n" % ctype)
        out.append(path.read_bytes())
        w("\r\n")
    w("--%s--\r\n" % boundary)
    return b"".join(out), "multipart/form-data; boundary=" + boundary
