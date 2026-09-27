"""Cloud annotation provider: FASTA -> pharokka/phold/phynteny -> local artifacts.

This is the only provider in RBPfinder that transmits the user's sequence off the
machine, so it is gated three ways and none of them is implicit:

  * the run's data policy must be `remote_allowed`;
  * the run must carry an explicit human authorisation (`--allow-remote`);
  * the caller must assert the genome is public.

Failure is always explicit. If submit, poll or retrieve fails, the provider
raises -- it never quietly falls back to a local provider, because a silent
downgrade would make a cloud-annotated run indistinguishable from a local one in
the audit trail.

Transport note: this machine sits behind a proxy that Python's ssl module cannot
negotiate with (`SSLEOFError` on handshake), while the system curl.exe, which
uses Schannel, works. So HTTP is delegated to curl rather than urllib. The
transport is a constructor argument so that a normal environment can use
anything else.
"""
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import time

DEFAULT_ENDPOINT = "https://phage-annotation.org"

# Artifacts pulled back for every job. Missing ones are recorded, not fatal:
# an absent phold table means STRUCTURAL_SCREEN_UNAVAILABLE downstream, which is
# an operating state, never a negative result.
ARTIFACTS = [
    "pharokka/pharokka.gbk",
    "pharokka/pharokka_cds_final_merged_output.tsv",
    "phold/phold_per_cds_predictions.tsv",
    "phynteny/phynteny.tsv",
]


class CloudAnnotationError(RuntimeError):
    """Any failure of submit / poll / retrieve. Never swallowed."""


class PolicyViolation(CloudAnnotationError):
    """The run is not permitted to transmit this sequence."""


def genome_sha256(fasta_path):
    """Hash the SEQUENCE, not the file: headers and line wrapping must not
    change the cache key, or the same genome would be resubmitted needlessly."""
    seq = []
    for line in pathlib.Path(fasta_path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith(">"):
            seq.append(line.strip().upper())
    return hashlib.sha256("".join(seq).encode()).hexdigest()


class CloudAnnotation:
    provider_id = "phage_annotation_org"
    transmits = True

    def __init__(self, cache_dir, endpoint=DEFAULT_ENDPOINT, curl_path=None,
                 poll_seconds=20, max_wait_seconds=5400, transport_attempts=6):
        self.endpoint = endpoint.rstrip("/")
        self.cache_dir = pathlib.Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.curl = curl_path or shutil.which("curl") or r"C:\Windows\System32\curl.exe"
        self.poll_seconds = poll_seconds
        self.max_wait_seconds = max_wait_seconds
        self.transport_attempts = transport_attempts

    # ---------------- transport ----------------

    # curl exit codes worth another attempt: they are transport failures, not the
    # service rejecting the request. The local proxy drops TLS handshakes sporadically
    # (35), which killed two panel submissions outright on the first run.
    RETRYABLE_EXITS = {7, 28, 35, 52, 55, 56}

    def _curl(self, args, out_path=None, timeout=180, attempts=None):
        attempts = attempts or self.transport_attempts
        cmd = [self.curl, "-sL", "--fail", "--retry", "3", "--retry-delay", "5",
               "--max-time", str(timeout)]
        if out_path:
            cmd += ["-o", str(out_path)]
        cmd += args

        last = None
        for i in range(attempts):
            p = subprocess.run(cmd, capture_output=True, text=True)
            if p.returncode == 0:
                return p.stdout
            last = p.returncode
            if p.returncode not in self.RETRYABLE_EXITS:
                break
            # retrying the SAME provider is not a fallback; the explicit-failure
            # contract is about never silently switching to a different source.
            time.sleep(min(120, 10 * (i + 1)))
        raise CloudAnnotationError(
            "curl exit %d after %d attempt(s) for %s" % (last, attempts, args[-1]))

    # ---------------- policy ----------------

    def _check_allowed(self, policy, public, authorised):
        if not public:
            raise PolicyViolation(
                "refusing to transmit: the genome is not declared public. "
                "Unpublished sequences must use Mode A with a local annotation.")
        if policy is not None and not policy.allows(self.provider_id)[0]:
            raise PolicyViolation(
                "refusing to transmit: data policy '%s' does not permit provider %s"
                % (getattr(policy, "mode", "?"), self.provider_id))
        if not authorised:
            raise PolicyViolation(
                "refusing to transmit: this run carries no explicit human "
                "authorisation (--allow-remote)")

    # ---------------- job lifecycle ----------------

    def _submit(self, fasta_path, gene_predictor, fast):
        args = ["-H", "Accept: application/json",
                "-F", "fasta=@%s" % fasta_path,
                "-F", "gene_predictor=%s" % gene_predictor]
        if fast:
            args += ["-F", "fast=on"]
        args += ["-w", "\\n%{http_code}\\n%{redirect_url}", self.endpoint + "/jobs"]
        body = self._curl(args, timeout=300)

        m = re.search(r"/jobs/([0-9a-f]{16,})", body)
        if not m:
            raise CloudAnnotationError(
                "submit did not return a job id; first 200 chars: %r" % body[:200])
        return m.group(1)

    def _wait(self, job_id):
        url = "%s/jobs/%s/status.json" % (self.endpoint, job_id)
        waited = 0
        last = None
        while waited < self.max_wait_seconds:
            try:
                raw = self._curl(["-H", "Accept: application/json", url], timeout=60)
                st = json.loads(raw)
            except (CloudAnnotationError, ValueError) as exc:
                # a transient poll failure is not a job failure; keep waiting
                st, last = {}, str(exc)[:120]
            state = (st.get("state") or st.get("status") or "").lower()
            if state in ("done", "complete", "completed", "finished", "success"):
                return st
            if state in ("failed", "error", "cancelled"):
                raise CloudAnnotationError("job %s reported state=%s" % (job_id, state))
            time.sleep(self.poll_seconds)
            waited += self.poll_seconds
        raise CloudAnnotationError(
            "job %s did not finish within %ds (last poll note: %s)"
            % (job_id, self.max_wait_seconds, last))

    def _retrieve(self, job_id, dest):
        dest.mkdir(parents=True, exist_ok=True)
        got, missing = {}, []
        for rel in ARTIFACTS:
            out = dest / pathlib.Path(rel).name
            try:
                self._curl(["%s/jobs/%s/files/%s" % (self.endpoint, job_id, rel)],
                           out_path=out, timeout=300)
            except CloudAnnotationError:
                missing.append(rel)
                if out.exists():
                    out.unlink()
                continue
            got[rel] = {
                "file": out.name,
                "bytes": out.stat().st_size,
                "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
            }
        if not got:
            raise CloudAnnotationError(
                "job %s produced no retrievable artifacts" % job_id)
        return got, missing

    # ---------------- public entry point ----------------

    def annotate(self, fasta_path, phage_id, public=False, authorised=False,
                 policy=None, gene_predictor="phanotate", fast=True,
                 terms_checked_date=None, refresh_missing=False):
        """Annotate one genome. Returns (artifact_dir, provenance dict)."""
        fasta_path = pathlib.Path(fasta_path)
        gsha = genome_sha256(fasta_path)
        dest = self.cache_dir / gsha
        prov_path = dest / "cloud_provenance.json"

        # --- cache: the same genome is never submitted twice
        if prov_path.exists():
            prov = json.loads(prov_path.read_text(encoding="utf-8"))
            prov["cache_hit"] = True
            # An artifact can be absent for two very different reasons: the job never
            # produced it, or its download hit a transient proxy failure. Re-attempting
            # ONLY the missing files costs no new submission and separates the two.
            if refresh_missing and prov.get("artifacts_missing"):
                still, recovered = [], {}
                for rel in prov["artifacts_missing"]:
                    out = dest / pathlib.Path(rel).name
                    try:
                        self._curl(["%s/jobs/%s/files/%s"
                                    % (self.endpoint, prov["job_id"], rel)],
                                   out_path=out, timeout=300, attempts=3)
                    except CloudAnnotationError:
                        still.append(rel)
                        if out.exists():
                            out.unlink()
                        continue
                    recovered[rel] = {
                        "file": out.name, "bytes": out.stat().st_size,
                        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                    }
                if recovered:
                    prov["raw_artifact_sha256"].update(recovered)
                    prov["artifacts_missing"] = still
                    prov["artifacts_recovered_on_refresh"] = sorted(recovered)
                    prov_path.write_text(
                        json.dumps(prov, indent=2, sort_keys=True) + chr(10),
                        encoding="utf-8")
            return dest, prov

        self._check_allowed(policy, public, authorised)

        submitted = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        job_id = self._submit(fasta_path, gene_predictor, fast)
        self._wait(job_id)
        got, missing = self._retrieve(job_id, dest)
        retrieved = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        prov = {
            "provider": self.provider_id,
            "provider_endpoint": self.endpoint,
            "provider_version_or_date": submitted[:10],
            "submission_time": submitted,
            "retrieval_time": retrieved,
            "genome_file": fasta_path.name,
            "genome_sha256": gsha,
            "phage_id": phage_id,
            "public_sequence": True,
            "remote_authorised": True,
            "authorisation_flag": "REMOTE_SUBMISSION_AUTHORIZED",
            "job_id": job_id,
            "raw_artifact_sha256": got,
            "artifacts_missing": missing,
            "terms_checked_date": terms_checked_date or submitted[:10],
            "gene_predictor": gene_predictor,
            "fast_mode": fast,
            "transport": "curl",
            "cache_hit": False,
        }
        prov_path.write_text(json.dumps(prov, indent=2, sort_keys=True) + "\n",
                             encoding="utf-8")
        return dest, prov
