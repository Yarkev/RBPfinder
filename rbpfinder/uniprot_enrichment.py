"""M20-a -- what UniProt says about the proteins Stage 2 already found.

Stage 2 searches UniProtKB and returns accessions. This asks UniProt what those
accessions are, and attaches the answer to the S record that produced them. It adds no
search, no family and no tier.

    BLAST evidence      "this candidate resembles some known protein"
    UniProt enrichment  "here is how that known protein is described"

The second explains the first and is not independent of it. A BLAST hit to a tail fiber
plus a UniProt name reading "tail fiber protein" is one observation described twice;
counting it twice would manufacture an independent family out of a single datum, which is
what `evidence_families.correlations` already forbids for C2 keywords and the annotator's
PHROG call.

Two failure modes this module is built to avoid, both of which this project has already
paid for once:

**A cross-reference is not an execution.** UniProt listing an InterPro or PDB entry means
another database holds a related record. It is context for a reader and a pointer for
later work. It is not a result RBPfinder obtained, it never lights up a capability, and
it is never family D or T.

**Enrichment failing must not damage what already exists.** If the lookup does not happen,
the S record from BLAST stands exactly as it was. An unreachable API is not a reason to
downgrade a search that succeeded.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .parse import accession as accession_mod

_BASE = "uniprot_enrichment"

# Cross-reference databases worth carrying through. A whitelist, so an unrecognised
# database is dropped rather than passed on as if we knew what it meant.
_XREF_KEEP = ("Pfam", "InterPro", "PDB", "SUPFAM", "Gene3D", "PROSITE", "CDD",
              "AlphaFoldDB", "SMART")


class EnrichmentUnavailable(Exception):
    """The lookup did not happen. Never a statement about the protein."""


def normalize(payload, database_release=None):
    """One UniProtKB JSON entry -> the frozen normalized record.

    Everything is read defensively: UniProt entries are heterogeneous, and a TrEMBL entry
    legitimately has almost nothing in it. Absent fields become empty, never guesses.
    """
    if not isinstance(payload, dict):
        raise EnrichmentUnavailable("the UniProt response is not a JSON object")

    acc = accession_mod.subject_identity(payload.get("primaryAccession") or "")
    names = []
    desc = payload.get("proteinDescription") or {}
    for slot in ("recommendedName", "submissionNames"):
        node = desc.get(slot)
        for item in (node if isinstance(node, list) else [node] if node else []):
            full = ((item or {}).get("fullName") or {}).get("value")
            if full:
                names.append(full)
    for alt in desc.get("alternativeNames") or []:
        full = ((alt or {}).get("fullName") or {}).get("value")
        if full:
            names.append(full)

    buckets = {"FUNCTION": [], "DOMAIN": [], "SUBUNIT": []}
    for comment in payload.get("comments") or []:
        kind = comment.get("commentType")
        if kind not in buckets:
            continue
        for text in comment.get("texts") or []:
            value = (text or {}).get("value")
            if value:
                buckets[kind].append(value)

    xrefs = []
    for ref in payload.get("uniProtKBCrossReferences") or []:
        db = ref.get("database")
        if db in _XREF_KEEP and ref.get("id"):
            xrefs.append({"database": db, "id": ref["id"],
                          # Stated on every single cross-reference, not once in a
                          # docstring, because this is the field most likely to be
                          # misread as "we ran that tool".
                          "is_association_not_execution": True})

    organism = (payload.get("organism") or {})
    return {
        "accession": acc,
        "entry_name": payload.get("uniProtkbId"),
        # Swiss-Prot vs TrEMBL. Carried, never assumed: an unreviewed entry's names are
        # frequently propagated automatically and deserve less weight.
        "reviewed": payload.get("entryType", "").startswith("UniProtKB reviewed"),
        "protein_names": names,
        "function_comments": buckets["FUNCTION"],
        "domain_comments": buckets["DOMAIN"],
        "subunit_comments": buckets["SUBUNIT"],
        "cross_references": xrefs,
        "organism": organism.get("scientificName"),
        "taxonomy": organism.get("taxonId"),
        "source": "uniprot",
        "retrieved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "database_release": database_release or "unknown",
    }


class UniProtEnricher(object):
    """Fetches and caches entries for accessions Stage 2 already returned."""

    def __init__(self, rules, opener=None):
        self.rules = rules
        self.endpoint = rules.get(_BASE + ".endpoint").rstrip("/")
        retry = rules.get(_BASE + ".transport_retry")
        self.attempts = retry["attempts"]
        self.backoff = retry["backoff_seconds"]
        self._opener = opener
        self._cache = {}                 # accession -> normalized record
        self.fetched = 0                 # network fetches actually performed
        self.failures = []

    def fetch(self, acc):
        """One accession. Cached, so a repeated accession costs nothing."""
        acc = accession_mod.subject_identity(acc)
        if acc in self._cache:
            return self._cache[acc]
        url = "%s/%s.json" % (self.endpoint,
                              urllib.parse.quote(str(acc), safe=""))
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "rbpfinder/1.x")
        opener = self._opener or urllib.request.urlopen
        last = None
        for attempt in range(self.attempts):
            try:
                with opener(req, timeout=60) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                    release = dict(resp.headers or {}).get("X-UniProt-Release")
                record = normalize(payload, release)
                self._cache[acc] = record
                self.fetched += 1
                return record
            except urllib.error.HTTPError as exc:
                # A 404 is an answer: UniProt has no such entry. Retrying will not
                # change it.
                raise EnrichmentUnavailable(
                    "UniProt returned HTTP %d for %s" % (exc.code, acc))
            except Exception as exc:                        # noqa: BLE001
                last = exc
                if attempt < self.attempts - 1:
                    time.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])
        raise EnrichmentUnavailable(
            "could not retrieve %s after %d attempts: %s"
            % (acc, self.attempts, str(last)[:120]))

    def enrich_record(self, s_record, hits, limit=None):
        """Attach enrichment to ONE S record. Returns the same record, mutated.

        The record is returned whatever happens. A lookup that fails leaves the BLAST
        evidence exactly as it was -- `not_run` is untouched, `direction` is untouched,
        and the failure is recorded as a limitation rather than as a finding.
        """
        if s_record.get("not_run"):
            # Nothing was searched, so there are no accessions to explain. Enriching
            # here would invent context for a search that did not happen.
            return s_record

        seen, records, failed = set(), [], []
        for hit in hits[:limit] if limit else hits:
            acc = accession_mod.subject_identity(hit.get("accession") or "")
            if not acc or acc in seen:
                continue                 # one accession is looked up once
            seen.add(acc)
            try:
                records.append(self.fetch(acc))
            except EnrichmentUnavailable as exc:
                failed.append({"accession": acc, "reason": str(exc)[:160]})

        raw = s_record.setdefault("raw", {})
        raw["uniprot_enrichment"] = {
            "family": "S",
            # Repeated in the artefact itself so a reader of the JSON, who never sees
            # this module, cannot mistake it for a second family.
            "adds_independent_family": False,
            "entries": records,
            "unavailable": failed,
            "accessions_enriched": len(records),
            "accessions_requested": len(seen),
        }
        if failed:
            self.failures.extend(failed)
        return s_record


def capability_note(record):
    """What the capability matrix may say about enrichment, and what it may not.

    Enrichment is not an independent capability and must never appear as InterProScan,
    HHpred or Foldseek having run. Any cross-reference it carries is an association.
    """
    enr = ((record or {}).get("raw") or {}).get("uniprot_enrichment") or {}
    xref_dbs = sorted({x["database"] for e in enr.get("entries") or []
                       for x in e.get("cross_references") or []})
    return {
        "enriched": bool(enr.get("entries")),
        "family": "S",
        "cross_referenced_databases": xref_dbs,
        "cross_references_are_executions": False,
    }
