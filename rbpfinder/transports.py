"""The three real transports, as thin adapters over what already works.

Each one answers two questions for the resolver and then, if chosen, fetches:

    available(spec) -> (bool, why)      asked BEFORE any byte exists
    source(spec)    -> str              what will be contacted
    fetch(spec, staging_dir, plan)      obtain bytes, describe them, stop

A fetcher does not build, publish, register, or decide the next transport. That list is
not style advice: every one of those, done here, would reopen a hole the runner closed.

They are adapters on purpose. The cursor path has a proven state machine with
query-bound resume that survived a destroyed 176 MB download; rewriting the three into
one "unified downloader" would throw that away to make an interface look neat.
"""
import datetime
import json
import pathlib
import shutil
import subprocess
import time
import urllib.parse

from .acquisition import TransportUnavailable
from .acquisition_runner import FetchResult, TransportFailure

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"
STREAM_URL = "https://rest.uniprot.org/uniprotkb/stream"
SEARCH_URL = "https://rest.uniprot.org/uniprotkb/search"


# ---- transport retry -------------------------------------------------------
# Measured 2026-08-25: a single TLS connect from this machine succeeds 5-8 times out of
# 10 against EVERY host, and bounded retry takes all of them to 10/10. Before this, a
# transient connect failure was being read as "the endpoint is unavailable" and recorded
# as an external blocker.
#
# LAST-RESORT defaults, for a caller holding no Rules object. The values that actually
# run come from YAML through `default_transports(rules)`. A constant that merely
# "mirrors" the YAML is precisely the drift Rules.get()/MissingRule exists to prevent:
# you edit the YAML, behaviour does not change, and nothing fails to tell you.
RETRY_ATTEMPTS = 5
RETRY_BACKOFF = (1, 2, 4, 8)

# Whitelist, never a blacklist -- the same rule as capability detection (HANDOFF 9.2).
# An unknown curl exit code is NOT retried: retrying something we have not classified
# is how a permanent error becomes a hang.
RETRYABLE_CURL_EXITS = {
    6,    # could not resolve host
    7,    # failed to connect
    28,   # operation timed out
    35,   # SSL connect error  <- the failure this machine actually produces
    52,   # empty reply from server
    55,   # failed sending network data
    56,   # failure receiving network data
}
RETRYABLE_HTTP = {429, 500, 502, 503, 504}


def _curl_retrying(args, attempts=None, backoff=None, what="request"):
    """Run curl, retrying transport failures and transient HTTP only.

    An ordinary 4xx is an answer about the request -- the query is wrong, the resource
    is gone -- and retrying it just delays the report. A dropped connection says nothing
    about the request at all, so it is worth asking again.

    Returns the completed process. Raises TransportFailure once the attempts are spent.
    Note what this does NOT do: it never reports back to the resolver, and it never
    suggests a different transport. A transport that ran and failed stays failed.
    """
    attempts = RETRY_ATTEMPTS if attempts is None else attempts
    backoff = RETRY_BACKOFF if backoff is None else backoff
    last = ""
    for i in range(attempts):
        proc = subprocess.run(args + ["-w", "%{http_code}"],
                              capture_output=True, text=True)
        code = (proc.stdout or "").strip().splitlines()[-1:] or [""]
        http = code[0].strip()
        if proc.returncode == 0:
            return proc, http
        retryable = (proc.returncode in RETRYABLE_CURL_EXITS
                     or (http.isdigit() and int(http) in RETRYABLE_HTTP))
        last = "curl exit %d, http %s: %s" % (proc.returncode, http or "-",
                                              (proc.stderr or "")[:160])
        if not retryable:
            raise TransportFailure(
                "%s failed and will not be retried (%s). A 4xx is an answer about the "
                "request, not a bad connection." % (what, last))
        if i < attempts - 1:
            time.sleep(backoff[min(i, len(backoff) - 1)])
    raise TransportFailure("%s failed %d times in a row; last: %s"
                           % (what, attempts, last))


def _retry_config(retry):
    """(attempts, backoff) from a YAML `transport_retry` mapping, or the defaults."""
    if not retry:
        return RETRY_ATTEMPTS, RETRY_BACKOFF
    return (retry.get("attempts", RETRY_ATTEMPTS),
            tuple(retry.get("backoff_seconds") or RETRY_BACKOFF))


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _curl():
    exe = shutil.which("curl") or shutil.which("curl.exe")
    if not exe:
        raise TransportUnavailable("curl was not found on PATH", capability="curl")
    return exe


def _count_and_size(path):
    n = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith(">"):
                n += 1
    return n, path.stat().st_size


class DirectCompressedTransport(object):
    """A single trusted bulk artefact -- preferred when one exists.

    For a UniProt taxonomy query none does: there is no stable, checksummed .fasta.gz
    for an arbitrary `taxonomy_id:N` slice. Reporting that as UNAVAILABLE is the correct
    answer, not an error, and `auto` proceeds to the stream. Inventing a fake direct
    source to make three transports "all succeed" would be a lie in the provenance.
    """

    resumable = False

    def __init__(self, retry=None):
        self.attempts, self.backoff = _retry_config(retry)

    def available(self, spec):
        if spec.expected_source and spec.expected_source.get("bulk_url") \
                and spec.expected_source.get("checksum"):
            return True, ""
        return False, ("no trusted bulk artefact defined for this DatabaseSpec "
                       "(needs a stable URL plus a checksum)")

    def source(self, spec):
        src = (spec.expected_source or {}).get("bulk_url")
        return src or "no bulk artefact"

    def fetch(self, spec, staging_dir, plan):
        src = (spec.expected_source or {}).get("bulk_url")
        if not src:
            # Reached only if a caller forced --method direct past the availability
            # check; still refuse rather than improvise a source.
            raise TransportUnavailable(
                "direct_compressed has no bulk artefact for this spec",
                capability="direct_compressed")
        staging_dir.mkdir(parents=True, exist_ok=True)
        out = staging_dir / "bulk.fasta.gz"
        started = _now()
        # Retries transport failures and 429/5xx; an ordinary 4xx raises immediately,
        # and no failure here ever causes a different transport to be chosen.
        _curl_retrying([_curl(), "-sSL", "--fail", "--max-time", "600",
                        "-o", str(out), src],
                       attempts=self.attempts, backoff=self.backoff,
                       what="the bulk artefact fetch")
        import gzip
        fasta = staging_dir / "payload.fasta"
        with gzip.open(out, "rt", encoding="utf-8", errors="replace") as src_fh, \
                fasta.open("w", encoding="utf-8", newline="\n") as dst:
            shutil.copyfileobj(src_fh, dst)
        n, size = _count_and_size(fasta)
        return FetchResult(
            method="direct_compressed", artifact_path=fasta, bytes_=size,
            record_count=n, retrieval_start=started, retrieval_end=_now(), source=src,
            fetcher_provenance={"protocol": "https-bulk", "version": "v1",
                                "decompressed_from": out.name})


class RestStreamTransport(object):
    """One /stream request for the whole result set."""

    resumable = False

    def __init__(self, retry=None):
        self.attempts, self.backoff = _retry_config(retry)

    def available(self, spec):
        if not shutil.which("curl") and not shutil.which("curl.exe"):
            return False, "curl was not found on PATH"
        return True, ""

    def source(self, spec):
        return "%s?query=%s" % (STREAM_URL,
                                urllib.parse.quote(spec.biological_query, safe=""))

    def fetch(self, spec, staging_dir, plan):
        staging_dir.mkdir(parents=True, exist_ok=True)
        fasta = staging_dir / "payload.fasta"
        hdr = staging_dir / "stream.headers"
        # Percent-encode the query. A real UniProt query contains spaces, parentheses
        # and `[150 TO 170]` ranges; interpolated raw, curl reads the brackets as a glob
        # range and rejects the URL before a single byte is sent (exit 3). Every fixture
        # query was simple enough to hide this -- the first real query found it at once.
        url = "%s?format=fasta&query=%s" % (
            STREAM_URL, urllib.parse.quote(spec.biological_query, safe=""))
        started = _now()
        # Retries transport failures and 429/5xx only; an ordinary 4xx raises at once.
        # A transport that ran and failed is still NOT a reason to switch transports:
        # the resolver is never consulted again.
        _curl_retrying([_curl(), "-sS", "--fail", "--max-time", "600",
                        "-D", str(hdr), "-o", str(fasta), url],
                       attempts=self.attempts, backoff=self.backoff,
                       what="the /stream endpoint")
        n, size = _count_and_size(fasta)
        return FetchResult(
            method="rest_stream", artifact_path=fasta, bytes_=size, record_count=n,
            retrieval_start=started, retrieval_end=_now(), source=url,
            fetcher_provenance={"protocol": "rest-stream", "version": "v1",
                                "max_time_seconds": 600})


class CursorPaginationTransport(object):
    """Fallback transport, and labelled as one.

    Not "the slow transport": the same script measured 1,160 and 8,700 records/minute on
    this machine hours apart. Throughput is a property of the proxy on the day, so it
    must not be encoded as a property of the method.

    Wraps the existing PowerShell fetcher, whose query-bound resume and
    truncate-to-last-good-boundary behaviour are already regression-tested.
    """

    resumable = True

    def available(self, spec):
        script = SCRIPTS / "fetch_until_complete.ps1"
        if not script.exists():
            return False, "fetch_until_complete.ps1 is not present"
        if not shutil.which("powershell") and not shutil.which("powershell.exe"):
            return False, "powershell was not found on PATH"
        return True, ""

    def source(self, spec):
        # Encoded like any other URL. This string is recorded as provenance, and
        # provenance that is not a URL anyone could re-issue is not provenance.
        return "%s?query=%s (cursor paging)" % (
            SEARCH_URL, urllib.parse.quote(spec.biological_query, safe=""))

    def fetch(self, spec, staging_dir, plan):
        staging_dir.mkdir(parents=True, exist_ok=True)
        name = "payload.fasta"
        started = _now()
        ps = shutil.which("powershell") or shutil.which("powershell.exe")
        cmd = [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               str(SCRIPTS / "fetch_until_complete.ps1"),
               "-Query", spec.biological_query, "-OutName", name,
               "-OutDir", str(staging_dir)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        fasta = staging_dir / name
        state_file = staging_dir / (name + ".state.json")
        # Truth comes from the state sidecar on disk, never from captured stdout -- the
        # lesson that cost a completed 176 MB download.
        state = None
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8-sig"))
            except ValueError:
                state = None
        if not fasta.exists():
            raise TransportFailure("the cursor fetcher produced no file (exit %d)"
                                   % proc.returncode)
        if state is None:
            raise TransportFailure("the cursor fetcher left no readable state sidecar")
        if (state.get("cursor") or "").strip():
            raise TransportFailure(
                "the download is incomplete: cursor still set at %s records; rerun to "
                "resume" % state.get("sequence_count"))
        n, size = _count_and_size(fasta)
        return FetchResult(
            method="cursor_pagination", artifact_path=fasta, bytes_=size, record_count=n,
            retrieval_start=state.get("retrieval_start") or started,
            retrieval_end=state.get("retrieval_end") or _now(),
            source=self.source(spec),
            resume_state={"query_hash": state.get("query_hash"),
                          "sequence_count": state.get("sequence_count")},
            fetcher_provenance={"protocol": "rest-cursor", "version": "v2",
                               "page_size": 500, "delay_seconds": 3,
                               "max_time_seconds": 45,
                               "transport_retry_owner": "outer_checkpointing_loop"})


def default_transports(rules=None):
    """Build the transports, taking retry policy from YAML when a Rules is supplied.

    `rules=None` is for callers that genuinely have none; it falls back to the module
    defaults rather than failing, because a missing Rules object is not the same kind of
    problem as a missing rule.
    """
    retry = rules.get("database_acquisition.transport_retry") if rules else None
    return {"direct_compressed": DirectCompressedTransport(retry=retry),
            "rest_stream": RestStreamTransport(retry=retry),
            "cursor_pagination": CursorPaginationTransport()}
