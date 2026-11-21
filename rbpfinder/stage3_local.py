"""Run Stage 3 (family T, structure) from a Foldseek install on this machine.

The Stage 2 counterpart (`stage2_local`) made local BLAST reachable from a plain command
without a hand-built manifest. This does the same for structure: a native Foldseek binary
plus a local pdb100/cath50 database turns family T from a manual AlphaFold-then-web round
into an inline local search. On the controllable Linux box this project is now being
packaged for, that is the single biggest lever on accuracy -- it is the evidence that
lifts a real RBP to R1 and that tells an apparatus protein apart from a binder.

Two rules, the same two `stage2_local` follows.

**The artefact, not an EvidenceRecord, is what this produces.** Each CDS gets a
Foldseek-web-shaped JSON file and a manifest row of kind `foldseek_json`. The ordinary
import path (`local_files` -> `structural.build`) reads it. One set of evidence semantics
for the local route and the frozen import route; no second interpreter.

**searched-no-hit is written, never omitted.** A CDS searched with no hit gets a JSON
file with an empty results array, so the manifest layer keeps "searched and found
nothing" distinct from "never searched" -- the invariant the whole product rests on.
"""
import json
import pathlib

from .providers.local_foldseek import LocalFoldseek, StructureDatabase


def known_db_labels(rules):
    """Labels Stage 3 will actually consult. A db under any other label is refused: it
    would be searched and then dropped by `structural.consensus`, wasting the run."""
    functional = list(rules.get("stage3.functional_databases"))
    apparatus = list(rules.get("stage3.apparatus_only_databases", []))
    return functional, apparatus


def _db_prefix_usable(path):
    """A Foldseek database is a prefix with sibling files. Accept the prefix if it or one
    of the files Foldseek always writes beside it exists -- the same shallow, read-only
    check `doctor` makes for a BLAST database (existence, not integrity)."""
    p = pathlib.Path(path)
    if p.exists():
        return True
    for suffix in (".dbtype", ".index", ".lookup", "_ss.dbtype"):
        if pathlib.Path(str(path) + suffix).exists():
            return True
    return False


# Taxonomy comes in TWO shapes and this check used to know only one of them.
#
# A database downloaded by `foldseek databases` ships a single `<prefix>_taxonomy` file
# (741 MB for PDB), and that is sufficient -- a real run resolved `taxname` from it.
# A database built by hand from PDB files has no taxonomy at all, and easy-search then
# dies at run time naming the files it wanted: `_mapping`, `_nodes.dmp`, `_names.dmp`,
# `_merged.dmp`.
#
# The first version of this check required that four-file set and nothing else, on the
# strength of a comment claiming the official download shipped it. It does not, and the
# claim was never verified -- it was an assumption written as a fact. The result was
# worse than the false positive it replaced: the official PDB database was refused with
# exit 2 before any search could run. Verified 2026-11-16 against both shapes.
TAXONOMY_SINGLE_SUFFIX = "_taxonomy"
TAXONOMY_DMP_SUFFIXES = ("_mapping", "_nodes.dmp", "_names.dmp", "_merged.dmp")

# Kept as the name the tests and doctor import; it names the fallback shape.
TAXONOMY_SUFFIXES = TAXONOMY_DMP_SUFFIXES


def taxonomy_missing(path):
    """Which taxonomy files this database prefix lacks. Empty when it is usable.

    Either shape satisfies it: the single `<prefix>_taxonomy` file that
    `foldseek databases` produces, or the four-file `.dmp` set. Only a database with
    neither is refused.
    """
    if pathlib.Path(str(path) + TAXONOMY_SINGLE_SUFFIX).exists():
        return []
    missing = [s for s in TAXONOMY_DMP_SUFFIXES
               if not pathlib.Path(str(path) + s).exists()]
    return [] if not missing else missing


def needs_taxonomy(rules):
    """Does the configured column contract ask for taxonomy?"""
    cols = rules.get("stage3.local_foldseek.format_output", []) or []
    return "taxname" in [str(c) for c in cols]


def resolve_structure_databases(rules, env=None, explicit=None):
    """Which structure databases apply, and why any configured one does not.

    `explicit` is a list of (label, path) from the command line. The environment variable
    RBPFINDER_FOLDSEEK_DB carries the same as "label=path[,label=path...]". Precedence is
    authority: a path given explicitly that does not resolve is an error recorded against
    that db, never a silent fall-through -- the discipline the executable resolver and the
    BLAST database resolver both keep.
    """
    env = env or {}
    functional, apparatus = known_db_labels(rules)
    allowed = set(functional) | set(apparatus)

    pairs = list(explicit or [])
    raw = env.get("RBPFINDER_FOLDSEEK_DB")
    if raw and not pairs:
        for chunk in raw.replace(";", ",").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                pairs.append((None, chunk))
            else:
                label, path = chunk.split("=", 1)
                pairs.append((label.strip(), path.strip()))

    use, decisions = [], []
    for label, path in pairs:
        if not label:
            decisions.append({"label": path, "used": False, "reason": "no_label",
                              "detail": "a structure database must be given as "
                                        "label=path, where label names what it is "
                                        "(e.g. pdb100); got a bare path"})
            continue
        if label not in allowed:
            decisions.append({"label": label, "used": False, "reason": "unknown_label",
                              "detail": "Stage 3 recognises %s; a database under %r "
                                        "would be searched and then dropped"
                                        % (", ".join(sorted(allowed)), label)})
            continue
        if not _db_prefix_usable(path):
            decisions.append({"label": label, "used": False, "reason": "invalid_path",
                              "detail": "no Foldseek database found at %s" % path,
                              "fatal": True})
            continue
        missing = taxonomy_missing(path) if needs_taxonomy(rules) else []
        if missing:
            decisions.append({
                "label": label, "used": False, "reason": "missing_taxonomy",
                "path": path, "fatal": True,
                "detail": "the database at %s has no taxonomy: neither <prefix>%s nor "
                          "the %s set. The column contract requests `taxname`, and "
                          "easy-search fails at run time against a database without it. "
                          "Use a database downloaded by `foldseek databases`, which "
                          "ships <prefix>%s."
                          % (path, TAXONOMY_SINGLE_SUFFIX,
                             ", ".join("<prefix>" + s for s in missing),
                             TAXONOMY_SINGLE_SUFFIX)})
            continue
        decisions.append({"label": label, "used": True, "path": path,
                          "reason": "configured",
                          "functional": label in functional})
        use.append(StructureDatabase(label, path))
    return use, decisions


def prepare(cds_rows, rules, capability, outdir, prostt5_model=None, env=None,
            explicit_dbs=None, sensitivity=None, restrict_ids=None):
    """Search the configured structure databases; return (rows, provenance).

    `rows` is a list of {cds_id, kind: 'foldseek_json', path} the caller adds to the run
    manifest. Returns ([], provenance) when no database applies -- the core analysis still
    completes and the provenance says why, exactly as Stage 2 does.

    `restrict_ids`, when given, limits the search to those CDS. Structure is the expensive
    evidence and, unlike BLAST, is treated as candidate-scoped everywhere else in the
    product (the AF3 and Foldseek manual batches are the nominated candidates, not the
    genome). Scoping the local search the same way bounds the ProstT5 cost on a machine
    with no GPU without changing any conclusion: a CDS no channel nominated is not an RBP
    region and would contribute a `not_run` family-T record either way. None means every
    CDS with a translation.
    """
    use, decisions = resolve_structure_databases(rules, env=env, explicit=explicit_dbs)
    prov = {"decisions": decisions, "databases_used": [d.label for d in use],
            "artifact_dir": None, "prostt5_model": prostt5_model,
            "scope": "all_cds" if restrict_ids is None else "nominated_pool"}

    fatal = [d for d in decisions if d.get("fatal")]
    if fatal:
        from .errors import InputValidationError
        raise InputValidationError(
            "a structure database was configured but is not usable: %s"
            % fatal[0]["detail"])
    if not use:
        prov["reason"] = "no applicable structure database; local Stage 3 did not run"
        return [], prov

    # Two different zeros, which this used to conflate into one silent one.
    #
    # "No CDS carries a protein sequence" is an INPUT problem: Mode B reads phold's
    # per-CDS TSV, which has no translation column at all, so every record was filtered
    # out here and the run reported `cds_searched: 0` with no warning anywhere. Measured
    # on a real genome that cost two true candidates -- a 985 aa fibre subunit and an
    # Ig-domain protein -- with nothing in the output to say family T had been dropped.
    # A structure database was configured, so the user had asked for this search.
    #
    # "No CDS is in the nominated pool" is a legitimate zero and stays silent: the pool
    # being empty is itself the answer, reported by Stage 1.
    with_seq = [r for r in cds_rows if r.get("translation")]
    if not with_seq:
        from .errors import InputValidationError
        raise InputValidationError(
            "local structure search was requested (a structure database is configured) "
            "but not one CDS carries a protein sequence, so family T could not be "
            "produced for anything. This is what phold's per-CDS TSV looks like: the "
            "table has no translation column.\n"
            "  Either run against the GenBank, which carries the sequences:\n"
            "      rbpfinder --genbank phold.gbk ...\n"
            "  or pass it alongside the TSV, to keep phold's per-CDS fields as well:\n"
            "      rbpfinder --phold phold_per_cds_predictions.tsv "
            "--phold-genbank phold.gbk ...\n"
            "  or state that you do not want structure evidence: --no-local-stage3")
    recs = [r for r in with_seq
            if restrict_ids is None or r["cds_id"] in restrict_ids]
    art_dir = pathlib.Path(outdir) / "stage3_local"
    art_dir.mkdir(parents=True, exist_ok=True)

    base = "stage3.local_foldseek"
    lf = LocalFoldseek(
        databases=use, prostt5_model=prostt5_model, capability=capability,
        cache_dir=str(art_dir / "cache"),
        sensitivity=sensitivity if sensitivity is not None
        else rules.get(base + ".default_sensitivity", 9.5),
        rules=rules)
    docs = lf.search_many(recs)

    rows = []
    hits = 0
    tophits = {}
    for rec in recs:
        cid = rec["cds_id"]
        doc = docs.get(cid, {"results": []})
        # Written on purpose even when empty: searched-no-hit must stay on disk.
        path = art_dir / (cid + ".foldseek.json")
        path.write_text(json.dumps(doc), encoding="utf-8")
        rows.append({"cds_id": cid, "kind": "foldseek_json", "path": str(path)})
        best = _best_target(doc)
        if best:
            hits += 1
            tophits[cid] = best

    # The best target per CDS, for the A2 capability probe. It is reported, never applied
    # here: whether these may count as a genome-wide T source is the audit's question,
    # and it still has to pass the structure-database whitelist.
    prov.update({"artifact_dir": str(art_dir), "cds_searched": len(rows),
                 "cds_with_hits": hits, "tophits": tophits})
    return rows, prov


def _best_target(doc):
    """The identifier of the first alignment in the document, or None.

    The identifier only -- `local_foldseek` joins it to the target's description with a
    space, and the whitelist is about the namespace, not the title.
    """
    for blk in doc.get("results") or []:
        alns = blk.get("alignments") or []
        flat = alns[0] if alns and isinstance(alns[0], list) else alns
        for aln in flat:
            target = str(aln.get("target") or "")
            if target:
                return target.split(" ", 1)[0]
    return None
