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

M19-f2 -- Galaxy Europe / Pharokka as an authenticated remote annotation provider.

Not a zero-configuration path, and the code says so rather than implying otherwise:
Galaxy refuses anonymous job creation (`POST /api/histories` -> 403), so every user needs
an account and an API key. What M19 promised was that a new user would not have to install
WSL, Ubuntu and Foldseek. That still holds. "Configure nothing at all" does not.

Two things this module is careful about, in order of how badly they would go wrong:

**The API key is a credential.** Unlike the EBI contact email it must never reach
`run_result.json`, provenance, the checkpoint, the event log, stdout, stderr, an exception
message, a fixture or an HTTP debug dump. Every error says `authentication failed` and
nothing more -- echoing a key or an `Authorization` header into a log is how a secret ends
up in an issue tracker.

**The lifecycle is non-blocking from the first line.** No `submit_and_wait()`. M19-d
already paid for that lesson with EBI: a call that blocks until finished leaves a live job
with no recoverable id when the process dies. Here the ids -- history, dataset, job -- are
returned so the caller can persist them and poll later.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from ..errors import CapabilityUnavailable, InputValidationError

_BASE = "genome_annotation"

# Poll outcomes, matching the Stage 2 transport vocabulary so one state machine can drive
# both. "not finished" and "failed" are different answers.
RUNNING, FINISHED, ERROR = "running", "finished", "error"

# The only thing an authentication problem is ever allowed to say.
AUTH_FAILED = ("authentication failed. Check the Galaxy API key supplied via "
               "--galaxy-api-key or GALAXY_API_KEY. RBPfinder does not display or log "
               "the key.")


class CredentialUnavailable(CapabilityUnavailable):
    """No usable API key. Never carries the key, or any part of it."""


class GalaxyAuthError(CapabilityUnavailable):
    """The service rejected the credential. Deliberately says nothing else."""


def resolve_api_key(explicit=None, env=None):
    """explicit CLI > environment > unavailable.

    An explicit value that is present but unusable is fatal, exactly as the BLAST
    resolver treats an explicit binary path. Falling back to the environment would mean
    the run silently used a different account than the one the user named, and the
    provenance would record a submission the user did not intend to make.

    Returns the key. Callers must not log, store or echo it.
    """
    env = os.environ if env is None else env
    if explicit is not None:
        if not str(explicit).strip():
            # Present and empty. NOT a reason to try the environment.
            raise CredentialUnavailable(
                "--galaxy-api-key was given but is empty. An explicit credential that "
                "does not work is a failure, not a reason to fall back to the "
                "environment -- that would submit under a different account than the "
                "one you named.", capability="galaxy_api_key")
        return str(explicit).strip()
    from_env = (env.get("GALAXY_API_KEY") or "").strip()
    if from_env:
        return from_env
    raise CredentialUnavailable(
        "remote annotation via Galaxy needs an API key. Pass --galaxy-api-key or set "
        "GALAXY_API_KEY. Create one in your Galaxy account settings; RBPfinder never "
        "invents, stores or logs it.", capability="galaxy_api_key")


def redact(text, key):
    """Last line of defence for anything that might reach a human.

    Applied at the boundary rather than trusted to every call site: the call site that
    forgets is the one that ends up in a bug report.
    """
    if not key or not text:
        return text
    return str(text).replace(str(key), "<redacted>")


class GalaxyPharokkaProvider(object):
    """Lifecycle primitives. Nothing here blocks until a job finishes."""

    provider_id = "galaxy_europe_pharokka"
    transmits = True

    def __init__(self, rules, api_key, endpoint=None, opener=None):
        cfg = rules.get(_BASE + ".providers.galaxy_europe_pharokka")
        self.rules = rules
        self.cfg = cfg
        self.endpoint = (endpoint or cfg["endpoint"]).rstrip("/")
        self.tool_id = cfg["tool_id"]
        self.tool_version = cfg["tool_version"]
        self.output_name = cfg["output"]
        self._key = api_key                 # never rendered anywhere
        self._opener = opener               # injectable for offline tests

    # -- disclosure --------------------------------------------------------
    def version_notice(self):
        """Shown BEFORE authorisation. A methodological difference that can move CDS
        boundaries and functional calls is not a footnote."""
        return self.cfg["version_notice"]

    def evidence_boundary(self):
        return dict(self.cfg["evidence_boundary"])

    def tos_gate_satisfied(self, confirmed):
        """The first real genome does not leave until a person has read the terms.

        `confirmed` comes from an explicit human acknowledgement, never from the absence
        of a prohibition -- a terms page that could not be read is not a permissive one.
        """
        if confirmed:
            return True
        gate = self.cfg["manual_tos_gate"]
        raise CapabilityUnavailable(
            "the usegalaxy.eu terms have not been confirmed for automated use. %s\n"
            "Until then: %s" % (gate["statement"].strip(), gate["until_then"]),
            capability="galaxy_tos_gate")

    # -- transport ---------------------------------------------------------
    def _request(self, method, path, data=None, raw=None, content_type=None):
        url = self.endpoint + path
        headers = {"x-api-key": self._key}
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif raw is not None:
            body = raw
            headers["Content-Type"] = content_type or "application/octet-stream"
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        opener = self._opener or urllib.request.urlopen
        try:
            with opener(req, timeout=120) as fh:
                text = fh.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                # Nothing about the key, the header, or the response body -- a 403 body
                # can echo request details.
                raise GalaxyAuthError(AUTH_FAILED, capability="galaxy_api_key")
            detail = ""
            try:
                detail = redact(exc.read().decode("utf-8", "replace")[:200], self._key)
            except Exception:                                   # noqa: BLE001
                pass
            raise CapabilityUnavailable(
                "Galaxy returned HTTP %d for %s: %s" % (exc.code, path, detail),
                capability="galaxy_europe_pharokka")
        except Exception as exc:                                # noqa: BLE001
            # Redacted even here: a socket error can carry the request line.
            raise CapabilityUnavailable(
                "Galaxy request to %s failed: %s"
                % (path, redact(str(exc)[:200], self._key)),
                capability="galaxy_europe_pharokka")
        return json.loads(text) if text.strip() else {}

    # Each of these returns identifiers for the caller to CHECKPOINT before going on.
    def create_history(self, name):
        return self._request("POST", "/api/histories", data={"name": name})["id"]

    def upload_fasta(self, history_id, fasta_path, name=None):
        """Returns the dataset id. The genome leaves the machine here, and only here."""
        import pathlib
        path = pathlib.Path(fasta_path)
        payload = {
            "history_id": history_id,
            "targets": [{
                "destination": {"type": "hdas"},
                "elements": [{"src": "files", "name": name or path.name,
                              "ext": "fasta"}],
            }],
        }
        return self._upload(payload, path)

    def _upload(self, payload, path):
        # Split out so tests can substitute it without reimplementing multipart.
        boundary = "----rbpfinder-upload-boundary"
        parts = []
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"history_id\"\r\n"
                      "\r\n%s\r\n" % (boundary, payload["history_id"])).encode("utf-8"))
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"targets\"\r\n\r\n"
                      "%s\r\n" % (boundary, json.dumps(payload["targets"])))
                     .encode("utf-8"))
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"files_0|file_data\";"
                      " filename=\"%s\"\r\nContent-Type: application/octet-stream\r\n\r\n"
                      % (boundary, path.name)).encode("utf-8"))
        parts.append(path.read_bytes())
        parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
        out = self._request("POST", "/api/tools/fetch", raw=b"".join(parts),
                            content_type="multipart/form-data; boundary=" + boundary)
        outputs = out.get("outputs") or []
        if not outputs:
            raise CapabilityUnavailable("Galaxy accepted no dataset for the upload",
                                        capability="galaxy_europe_pharokka")
        return outputs[0]["id"]

    def run_pharokka(self, history_id, dataset_id, params=None):
        """Returns the job id. Does NOT wait."""
        inputs = {"fasta": {"src": "hda", "id": dataset_id}}
        inputs.update(params or {})
        out = self._request("POST", "/api/tools", data={
            "history_id": history_id, "tool_id": self.tool_id, "inputs": inputs})
        jobs = out.get("jobs") or []
        if not jobs:
            raise CapabilityUnavailable("Galaxy started no job for the Pharokka tool",
                                        capability="galaxy_europe_pharokka")
        return jobs[0]["id"], [o["id"] for o in out.get("outputs") or []]

    def poll_once(self, job_id):
        """One status check. RUNNING / FINISHED / ERROR, never blocking."""
        state = self._request("GET", "/api/jobs/" + urllib.parse.quote(job_id, safe=""))
        st = (state or {}).get("state")
        if st == "ok":
            return FINISHED
        if st in ("error", "deleted", "failed", "paused"):
            return ERROR
        return RUNNING

    def fetch_genbank(self, job_id, dest_path):
        """Download the `pharokka_gbk` output. Idempotent; safe after a restart."""
        job = self._request("GET", "/api/jobs/%s?full=true"
                            % urllib.parse.quote(job_id, safe=""))
        outputs = (job or {}).get("outputs") or {}
        target = outputs.get(self.output_name)
        if not target:
            raise CapabilityUnavailable(
                "the Pharokka job produced no %s output; without a GenBank there is "
                "nothing to analyse" % self.output_name,
                capability="galaxy_europe_pharokka")
        content = self._request(
            "GET", "/api/datasets/%s/display?to_ext=genbank"
                   % urllib.parse.quote(target["id"], safe=""))
        import pathlib
        pathlib.Path(dest_path).write_text(
            content if isinstance(content, str) else json.dumps(content),
            encoding="utf-8", newline="\n")
        return pathlib.Path(dest_path)

    def provenance(self):
        """Everything worth recording, and deliberately not the key."""
        return {
            "provider": self.provider_id,
            "endpoint": self.endpoint,
            "tool": self.cfg["tool"],
            "tool_version": self.tool_version,
            "tool_id": self.tool_id,
            "classification": self.cfg["classification"],
            "zero_configuration": self.cfg["zero_configuration"],
            "evidence_boundary": self.evidence_boundary(),
            # No api_key field exists here, and none may be added. The gates in
            # scripts/test_galaxy_credentials.py assert its absence.
        }
