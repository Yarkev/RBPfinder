"""Execute an AcquisitionPlan: fetch, verify, build, verify, publish, register.

C-2a decided HOW to obtain a database. This module does it, and its whole job is to make
the second half identical no matter which transport produced the FASTA:

    FetchResult -> source integrity -> build -> BLAST validation -> atomic publish
                -> register through the M15C-1 configure path

Three exception classes, and the difference between them is the point of the milestone:

    TransportUnavailable  this route cannot run here (bulk endpoint absent, proxy blocks
                          paging). Only the RESOLVER acts on this, and only at planning
                          time, before any byte exists.
    TransportFailure      the chosen route ran and failed -- timeout, dropped connection.
                          The fetcher's own frozen retry/resume policy handles it. The
                          resolver is NOT consulted again; we do not change transport
                          because this one had a bad minute.
    IntegrityFailure      the data we hold is wrong -- checksum, record count, state
                          inconsistency. HARD FAIL, no retry, no fallback.

The path this file exists to make unreachable:

    stream downloads a corrupt file -> runner catches Exception -> "let's try cursor"

which would hand back a database that looks acquired. The runner therefore calls
`resolver.plan()` exactly once, before fetching, and never catches a bare Exception.

Fetchers stay narrow. A fetcher obtains bytes and describes them; it does not call
makeblastdb, does not publish, does not write configuration, and does not choose the
next transport.
"""
import hashlib
import pathlib
import subprocess

from . import database
from .acquisition import (IntegrityFailure, Staging, TransportUnavailable,
                          verification_record)
from .capability import BlastCapability
from .errors import RBPFinderError


class TransportFailure(RBPFinderError):
    """The chosen transport ran and failed. Retry/resume belongs to that fetcher."""
    exit_code = 3


class FetchResult(object):
    """What a fetcher obtained. The same shape for every transport."""

    FIELDS = ("method", "artifact_path", "bytes", "record_count", "retrieval_start",
              "retrieval_end", "source", "checksum", "resume_state",
              "fetcher_provenance")

    def __init__(self, method, artifact_path, bytes_=None, record_count=None,
                 retrieval_start=None, retrieval_end=None, source=None, checksum=None,
                 resume_state=None, fetcher_provenance=None):
        self.method = method
        self.artifact_path = pathlib.Path(artifact_path)
        self.bytes = bytes_
        self.record_count = record_count
        self.retrieval_start = retrieval_start
        self.retrieval_end = retrieval_end
        self.source = source
        self.checksum = checksum
        self.resume_state = resume_state
        self.fetcher_provenance = fetcher_provenance or {}

    def describe(self):
        return {f: (str(getattr(self, f)) if f == "artifact_path"
                    else getattr(self, f)) for f in self.FIELDS}


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def count_fasta(path):
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(">"):
                n += 1
    return n


def validate_source(spec, result):
    """Integrity of the fetched bytes. Every failure here is terminal.

    Deliberately does not know which transport produced the file: a shortfall of records
    means the same thing whether it arrived in one gzip or 1,129 cursor pages.
    """
    if not result.artifact_path.exists():
        raise IntegrityFailure("the fetcher reported success but %s does not exist"
                               % result.artifact_path)
    observed_bytes = result.artifact_path.stat().st_size
    if observed_bytes == 0:
        raise IntegrityFailure("fetched artefact is empty: %s" % result.artifact_path)
    observed_records = count_fasta(result.artifact_path)
    digest = sha256_file(result.artifact_path)

    if spec.checksum and spec.checksum != digest:
        raise IntegrityFailure(
            "checksum mismatch for %s: expected %s, got %s"
            % (result.artifact_path.name, spec.checksum, digest))
    if spec.expected_records:
        ratio = observed_records / float(spec.expected_records)
        if not 0.97 <= ratio <= 1.03:
            raise IntegrityFailure(
                "record count %d is %.1f%% of the expected %d"
                % (observed_records, 100 * ratio, spec.expected_records))
    if result.record_count is not None and result.record_count != observed_records:
        raise IntegrityFailure(
            "the fetcher claims %d records; the file holds %d"
            % (result.record_count, observed_records))
    return {"records": observed_records, "bytes": observed_bytes, "sha256": digest}


class BlastToolchain(object):
    """Build and validate. Injectable so fixtures need no BLAST installation."""

    def __init__(self, capability=None):
        self.capability = capability or BlastCapability()

    def build(self, fasta, out_prefix, title=None):
        exe = self.capability.require("makeblastdb")["makeblastdb"]
        cmd = [exe, "-in", str(fasta), "-dbtype", "prot", "-parse_seqids",
               "-out", str(out_prefix)]
        if title:
            cmd += ["-title", title]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise IntegrityFailure("makeblastdb failed (%d): %s"
                                   % (proc.returncode, (proc.stderr or "")[:300]))
        return out_prefix

    def validate(self, prefix, expected_records):
        status, detail, _meta = database.inspect(str(prefix))
        if status != database.STATUS_AVAILABLE:
            raise IntegrityFailure("the built database is not usable: %s" % detail)
        return verification_record(True, "blastdbcmd" in detail,
                                   detail if "blastdbcmd" in detail else None)


def run(spec, plan, fetcher, final_prefix, toolchain=None, register=True):
    """The one path every transport ends up in.

    `plan` is passed in already decided. The runner never re-plans, which is what makes
    "fetch failed, try another transport" structurally impossible rather than merely
    discouraged.
    """
    toolchain = toolchain or BlastToolchain()
    staging = Staging(plan, spec)
    staging.write("STAGING")

    # The fetcher owns its own retries and resume. TransportFailure propagates; it is
    # never converted into a different transport choice.
    result = fetcher.fetch(spec, staging.dir, plan)
    staging.write("FETCHED", fetch=result.describe())

    observed = validate_source(spec, result)          # IntegrityFailure = terminal
    staging.write("FETCH_VALIDATED", observed=observed)

    built = toolchain.build(result.artifact_path, staging.dir / "db",
                            title="%s (%s)" % (spec.role, spec.biological_query))
    staging.write("BUILT", built_prefix=str(built))

    verification = toolchain.validate(built, observed["records"])
    metadata = build_metadata(spec, result, observed, verification)
    staging.write("BUILD_VALIDATED", verification=verification, metadata=metadata)

    published, moved = staging.publish(built, final_prefix)
    write_metadata(published, metadata)

    if register:
        # The ONE registration path. C-2b never writes the configuration file itself.
        database.configure(spec.role, str(published))
    return {"published": str(published), "files": moved, "metadata": metadata,
            "verification": verification}


def build_metadata(spec, result, observed, verification):
    """Identical for every transport except the source fields.

    Database identity -- what was asked for, how many records arrived, what the bytes
    hash to -- cannot depend on how the bytes travelled. Only `acquisition` may.
    """
    return {
        "database_role": spec.role,
        "query": spec.biological_query,
        "taxonomy": spec.taxonomy,
        "release_or_snapshot": spec.release_or_snapshot,
        "spec_hash": spec.spec_hash,
        "sequence_count": observed["records"],
        "fasta_bytes": observed["bytes"],
        "fasta_sha256": observed["sha256"],
        "verification": verification,
        "acquisition": {
            "method": result.method,
            "source": result.source,
            "retrieval_start": result.retrieval_start,
            "retrieval_end": result.retrieval_end,
            "resume_state": result.resume_state,
            "fetcher_provenance": result.fetcher_provenance,
        },
    }


IDENTITY_FIELDS = ("database_role", "query", "taxonomy", "release_or_snapshot",
                   "spec_hash", "sequence_count", "fasta_bytes", "fasta_sha256")


def write_metadata(prefix, metadata):
    import yaml
    p = pathlib.Path(str(prefix) + ".metadata.yaml")
    p.write_text("# frozen database provenance -- written by acquisition\n" +
                 yaml.safe_dump(metadata, sort_keys=True, allow_unicode=True),
                 encoding="utf-8")
    return p
