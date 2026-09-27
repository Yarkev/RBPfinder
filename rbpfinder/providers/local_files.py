"""Local Stage 2 providers -- results already on disk, nothing uploaded.

The executor asks a provider for evidence about a CDS and receives normalised
EvidenceRecords. It never learns whether they came from EBI, NCBI, a local
BLAST or a hand-imported .hhr, which is what lets the remote path stay off by
default for unpublished genomes.

A provider is fed by a manifest TSV:

    cds_id  kind  path

where kind is one of uniprot_blast_tsv | interproscan_json | hhpred_hhr.
An explicit manifest, rather than a directory-scan convention, keeps the
provenance of every piece of evidence auditable.
"""
import csv
import pathlib

from ..errors import InputValidationError
from .. import return_binding
from ..parse import uniprot_blast, interproscan, hhpred, foldseek, blast_tabular
from ..evidence import sequence_homology, domain_profile, structural

KINDS = ("uniprot_blast_tsv", "local_blast_tsv", "remote_blast_tsv",
         "interproscan_json", "hhpred_hhr", "foldseek_json")

_PROVIDER_ID = {
    "uniprot_blast_tsv": "uniprot_blastp",
    "local_blast_tsv": "local_blast_db",
    # the S record says which database actually answered; a remote hit must not be
    # reported as though a local BLAST database produced it
    "remote_blast_tsv": "ebi_blastp",
    "interproscan_json": "interproscan",
    "hhpred_hhr": "hhpred",
    "foldseek_json": "foldseek_structure",
}

# every kind here reads a result that already exists on disk, so none of them
# transmit anything -- they map onto the cached providers in the registry
_REGISTRY_ID = {
    "uniprot_blast_tsv": "existing_blast_import",
    "local_blast_tsv": "existing_local_blast_import",
    "remote_blast_tsv": "existing_remote_blast_import",
    "interproscan_json": "existing_interpro_import",
    "hhpred_hhr": "existing_hhr_import",
    "foldseek_json": "existing_foldseek_import",
}


def _merge_sequence_hits(*hit_lists):
    """Fold every sequence-search result for one CDS into ONE ranked hit list.

    evidence_families.correlations is explicit that DB_PHAGE and DB_HOST hits merge
    into a single S unit: the two databases answer the same question with the same
    method, so consulting both must not manufacture a second independent family.
    The same reasoning covers an imported EBI table sitting beside a local BLAST
    result. Merging here, before sequence_homology.build sees anything, is what makes
    that structural rather than a rule someone has to remember downstream.

    Ordering is by e-value so the consensus reads the same hits regardless of which
    database supplied them; a hit with no e-value sorts last rather than first.
    Duplicate accessions are collapsed, keeping the better e-value.
    """
    best = {}
    order = []
    for hits in hit_lists:
        for h in hits or []:
            acc = h.get("accession")
            ev = h.get("evalue")
            prev = best.get(acc)
            if prev is None:
                best[acc] = dict(h)
                order.append(acc)
            elif ev is not None and (prev.get("evalue") is None or ev < prev["evalue"]):
                best[acc] = dict(h)
    merged = [best[a] for a in order]
    merged.sort(key=lambda h: (h.get("evalue") is None,
                               h.get("evalue") if h.get("evalue") is not None else 0.0))
    for i, h in enumerate(merged, 1):
        h["rank"] = i
    return merged


_REQUIRED_COLUMNS = ("cds_id", "kind", "path")


class Manifest:
    """The index of Stage 2 artefacts, and the place three states are kept apart.

        no entry for a CDS              -> the provider never ran        (not_run)
        entry + artefact of zero bytes  -> it ran and found nothing      (searched)
        entry + artefact missing        -> the input is incomplete       (InputValidationError)

    The third case must never decay into the second. `empty result != absent result` is
    the invariant this project has already had to re-establish twice; letting a missing
    file read as "searched, no hits" would break it at the installation layer, where it
    is hardest to notice.
    """

    def __init__(self, path=None):
        self.entries = {}
        # M21-c. The rows are kept whole, not reduced to {cds_id: {kind: path}} on the
        # way in: `sequence_sha256` and `batch_id` are needed to judge whether a row may
        # be imported at all, and that judgement happens after parsing, in
        # `return_binding`. Reducing first would throw away the evidence for it.
        self.rows = []
        self.fieldnames = []
        self.path = pathlib.Path(path) if path else None
        if not self.path or not self.path.exists():
            return
        # utf-8-sig, once, here. Windows tools -- PowerShell, Excel, Notepad -- write a
        # BOM by default, and until M15B-3 that produced `KeyError: 'cds_id'` from deep
        # inside a csv reader. The downloader learned this lesson already; the manifest
        # reader had not.
        with self.path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            # header first: validating on the first data row means a file with a bad
            # header but no rows looks fine, and the error names a row instead of a column
            self.fieldnames = list(reader.fieldnames or [])
            missing = [c for c in _REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise InputValidationError(
                    "%s is missing required column%s %s"
                    % (self.path.name, "" if len(missing) == 1 else "s",
                       ", ".join("'%s'" % c for c in missing)),
                    path=str(self.path))
            for line_no, r in enumerate(reader, start=2):
                kind = r.get("kind")
                if kind not in KINDS:
                    raise InputValidationError(
                        "%s line %d: unknown kind %r (expected one of %s)"
                        % (self.path.name, line_no, kind, ", ".join(KINDS)),
                        path=str(self.path), line=line_no)
                if not r.get("cds_id") or not r.get("path"):
                    raise InputValidationError(
                        "%s line %d: cds_id and path must both be present"
                        % (self.path.name, line_no), path=str(self.path), line=line_no)
                if not pathlib.Path(r["path"]).exists():
                    raise InputValidationError(
                        "%s line %d references a missing artefact: %s"
                        % (self.path.name, line_no, r["path"]),
                        path=str(self.path), line=line_no)
                row = dict(r)
                row["_source"] = self.path.name
                row["_line"] = line_no
                self.rows.append(row)
                self.entries.setdefault(r["cds_id"], {})[kind] = r["path"]

    def add(self, cds_id, kind, path):
        """Register an artefact produced during THIS run.

        The remote backend learns about candidates round by round -- Stage 6 reopens a
        CDS, it gets searched, and its artefact has to become visible to the next
        classify pass. Growing the index in place keeps one manifest contract for both
        backends instead of a second, parallel way of reaching evidence.
        """
        if kind not in KINDS:
            raise InputValidationError("unknown manifest kind %r" % kind)
        if not pathlib.Path(path).exists():
            raise InputValidationError(
                "refusing to register a missing artefact for %s: %s" % (cds_id, path))
        self.entries.setdefault(cds_id, {})[kind] = str(path)
        self.rows.append({"cds_id": cds_id, "kind": kind, "path": str(path),
                          "_source": "this run"})

    def files_for(self, cds_id):
        return self.entries.get(cds_id, {})

    def bound_schema(self):
        """Does this manifest claim to carry bindings? See return_binding.is_bound_schema."""
        return return_binding.is_bound_schema(self.fieldnames)

    def drop(self, cds_id, kind):
        """Remove a row that failed its binding check, before anything parses it.

        Removed rather than flagged: `collect()` reads `files_for()`, and a row left in
        place would be parsed regardless of any verdict recorded elsewhere. The verdict
        is kept by the caller and reported; what must not survive is the file reaching a
        parser.
        """
        entry = self.entries.get(cds_id) or {}
        entry.pop(kind, None)
        if not entry:
            self.entries.pop(cds_id, None)
        self.rows = [r for r in self.rows
                     if not (r.get("cds_id") == cds_id and r.get("kind") == kind)]

    def __len__(self):
        return len(self.entries)


def collect(rec, manifest, rules, policy=None):
    """Return (records, extras) for one CDS.

    Exactly one S record and one D record are always produced. When no result
    exists the record is `uninformative` -- missing evidence is stated, never
    silently dropped, so it can never read as a negative finding.
    """
    files = dict(manifest.files_for(rec["cds_id"]))
    records, extras, blocked = [], {}, []
    if policy is not None:
        for kind in list(files):
            ok, why = policy.allows(_REGISTRY_ID[kind])
            if not ok:
                files.pop(kind)
                blocked.append("%s: %s" % (kind, why))
    if blocked:
        extras["policy_blocked"] = blocked

    blast_path = files.get("uniprot_blast_tsv")
    local_path = files.get("local_blast_tsv")
    remote_path = files.get("remote_blast_tsv")
    imported = uniprot_blast.read(blast_path) if blast_path else []
    # blast_tabular keys by query id, so a per-CDS file that also holds other queries
    # cannot leak their hits into this record.
    local = (blast_tabular.read(local_path).get(rec["cds_id"], []) if local_path else [])
    remote = (blast_tabular.read(remote_path).get(rec["cds_id"], []) if remote_path else [])
    used = [k for k in ("uniprot_blast_tsv", "local_blast_tsv", "remote_blast_tsv")
            if files.get(k)]
    hits = (_merge_sequence_hits(imported, local, remote) if len(used) > 1
            else (imported or local or remote))
    s_provider = "+".join(_PROVIDER_ID[k] for k in used) or _PROVIDER_ID["uniprot_blast_tsv"]
    # An entry in the manifest IS the record that a search ran for this CDS: the file
    # was written by a search, and an empty one says "nothing was found", not "nobody
    # looked". Without this bit an exhausted candidate is indistinguishable from an
    # untouched one and the cost funnel inverts -- see sequence_homology.build.
    s_rec, cons = sequence_homology.build(rec, hits, rules, s_provider,
                                          searched=bool(used))
    records.append(s_rec)
    if cons:
        extras["homolog_consensus"] = cons

    ipr_path = files.get("interproscan_json")
    hhr_path = files.get("hhpred_hhr")
    if hhr_path:
        # HHpred supersedes InterProScan on conflict, per evidence_families
        records.append(domain_profile.from_hhpred(
            rec, hhpred.read(hhr_path), rules, _PROVIDER_ID["hhpred_hhr"]))
    # M20-b2E4: `from_interproscan` returns a LIST of region-level records now, one per
    # hit, so these extend rather than append. One artifact can contribute several D
    # records; de-correlation still collapses them to at most one independent D.
    if ipr_path:
        records.extend(domain_profile.from_interproscan(
            rec, interproscan.read(ipr_path), rules, _PROVIDER_ID["interproscan_json"]))
    if not hhr_path and not ipr_path:
        records.extend(domain_profile.from_interproscan(
            rec, [], rules, "domain_profile_absent"))

    # --- Stage 3, family T --------------------------------------------------
    fs_path = files.get("foldseek_json")
    t_rec, t_cons = structural.build(
        rec, foldseek.read(fs_path) if fs_path else [], rules,
        _PROVIDER_ID["foldseek_json"])
    records.append(t_rec)
    if t_cons:
        extras["structural_consensus"] = t_cons

    return records, extras


def hhpred_manifest_rows(candidates, by_id, rules):
    """Rows for the HHPRED_BATCH human breakpoint.

    Same shape as the AF3 breakpoint: the executor emits a request list, a human
    runs the web service, the results come back as .hhr files.
    """
    import hashlib
    fields = rules.get("stage2.hhpred.manifest_fields")
    rows = []
    for c in candidates:
        rec = by_id[c["cds_id"]]
        seq = rec.get("translation") or ""
        rows.append({
            "candidate_id": c["cds_id"],
            "sequence_sha256": hashlib.sha256(seq.encode()).hexdigest() if seq else "",
            "requested_reason": "tier %s-%s with families %s; profile evidence would be decisive"
                                % (c["tier_r"], c["tier_e"],
                                   "+".join(c.get("independent_families") or ["-"])),
        })
    return fields, rows
