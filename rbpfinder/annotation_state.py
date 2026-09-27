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

M19-f2 -- persistent state for one annotation attempt. The recovery source.

Same discipline as `search_state.py`, and for the same reason: an annotation is a
multi-step remote lifecycle, so a process killed halfway must leave something that can be
resumed rather than a stranded history on someone else's server.

Two decisions carried over from earlier mistakes:

**One canonical identity.** M19-d shipped broken for a while because state was written
under one hash and read under another. Galaxy offers three tempting identifiers --
`history_id`, `dataset_id`, `job_id` -- and none of them is the identity of the work.
They are *locators for this attempt*. The identity is what makes two annotations the same
annotation:

    provider + input_fasta_sha256 + tool_id + annotation_config_sha256

**Configuration is part of the identity.** Same FASTA, different `gene_predictor`, is a
different annotation. Reusing the old result under the new settings would silently answer
a question nobody asked -- the same failure as reusing a sequence search after the
protein changed.
"""
import hashlib
import json
import os
import pathlib
import tempfile
import time

# Lifecycle states. Each remote step gets its own, because each is a place a process can
# die while leaving something real on the server.
PENDING = "pending"
HISTORY_CREATED = "history_created"
UPLOADED = "uploaded"
SUBMITTED = "submitted"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"

TRUSTED_COMPLETE = (COMPLETED,)
# Anything with a remote locator worth returning to rather than recreating.
RESUMABLE = (HISTORY_CREATED, UPLOADED, SUBMITTED, RUNNING)

# Parameters that change what the annotation MEANS. A change here invalidates reuse.
# Deliberately a whitelist: an unknown Galaxy parameter is not silently assumed harmless,
# it is included, so the fingerprint changes and the work is redone.
CONFIG_KEYS_ALWAYS = ("tool_id", "tool_version")


def config_sha256(params, tool_id, tool_version):
    """Fingerprint of everything that changes the annotation's meaning."""
    payload = {"tool_id": tool_id, "tool_version": tool_version,
               "params": {k: params[k] for k in sorted(params or {})}}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def annotation_identity(provider, input_fasta_sha256, tool_id, config_sha):
    """The one key. Remote locators are never part of it."""
    blob = "%s|%s|%s|%s" % (provider, input_fasta_sha256, tool_id, config_sha)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def file_sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class AnnotationState(object):
    """One JSON file, rewritten atomically on every transition."""

    VERSION = 1

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.entries = {}
        self.corrupt = False
        self.corrupt_reason = ""
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as exc:
            self.corrupt = True
            self.corrupt_reason = "annotation checkpoint is not valid JSON: %s" % exc
            return
        if not isinstance(data, dict) or data.get("version") != self.VERSION:
            self.corrupt = True
            self.corrupt_reason = "annotation checkpoint version mismatch"
            return
        self.entries = data.get("annotations") or {}

    def _write(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": self.VERSION, "updated_at": _now(),
                   "annotations": self.entries}
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

    def _set(self, key, **fields):
        entry = self.entries.setdefault(key, {})
        entry.update(fields)
        entry["updated_at"] = _now()
        self._write()                      # every transition, never batched
        return entry

    # -- transitions, one per remote step ----------------------------------
    def declare(self, key, provider, input_fasta_sha256, tool_id, config_sha):
        if key in self.entries:
            return self.entries[key]
        return self._set(key, provider=provider,
                         input_fasta_sha256=input_fasta_sha256, tool_id=tool_id,
                         annotation_config_sha256=config_sha, state=PENDING,
                         history_id=None, dataset_id=None, job_id=None)

    def history_created(self, key, history_id):
        return self._set(key, state=HISTORY_CREATED, history_id=history_id)

    def uploaded(self, key, dataset_id):
        return self._set(key, state=UPLOADED, dataset_id=dataset_id)

    def submitted(self, key, job_id):
        return self._set(key, state=SUBMITTED, job_id=job_id)

    def running(self, key):
        return self._set(key, state=RUNNING)

    def completed(self, key, genbank_path, tool_version=None):
        return self._set(key, state=COMPLETED,
                         annotated_genbank_path=str(genbank_path),
                         tool_version=tool_version)

    def failed(self, key, reason):
        return self._set(key, state=FAILED, failure_reason=str(reason)[:200])

    # -- queries -----------------------------------------------------------
    def state_of(self, key):
        return (self.entries.get(key) or {}).get("state")

    def entry(self, key):
        return dict(self.entries.get(key) or {})

    def reusable(self, key, input_fasta_sha256, config_sha):
        """A completed annotation may be reused only if BOTH the input and the
        configuration still match.

        The config half is the one that is easy to forget: same genome, different gene
        predictor, is a different annotation, and handing back the old GenBank would
        answer a question nobody asked.
        """
        e = self.entries.get(key) or {}
        if e.get("state") not in TRUSTED_COMPLETE:
            return False, "no completed annotation for this identity"
        if e.get("input_fasta_sha256") != input_fasta_sha256:
            return False, "the input FASTA has changed"
        if e.get("annotation_config_sha256") != config_sha:
            return False, "the annotation configuration has changed"
        return True, "reusable"


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")
