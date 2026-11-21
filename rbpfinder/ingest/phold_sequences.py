"""Attach protein sequences to Mode B records from the GenBank phold wrote beside them.

phold's `phold_per_cds_predictions.tsv` has no translation column, so Mode B records carry
no protein sequence and local structure search has nothing to search. Before this existed
that produced `cds_searched: 0` in silence; `stage3_local.prepare` now refuses instead,
and this is the route that makes the TSV usable rather than merely refused.

Why bother, when `--genbank phold.gbk` already works: the TSV carries per-CDS fields the
GenBank does not -- `prostt5_confidence`, `qCov`, the per-category bitscore proportions --
so TSV plus GenBank is a superset of either alone, not a detour.

The matching is deliberately strict. A GenBank from a *different* annotation run would
attach sequences to the wrong coordinates, and a protein searched under its neighbour's
identity is worse than no family T at all: it is a confident wrong answer. So coordinates
decide, the id is a cross-check, and a poor overlap is refused rather than reported.
"""
from ..errors import InputValidationError
from . import genbank as genbank_in

# Below this fraction of Mode B records matched, the two files are treated as describing
# different annotations rather than as an incomplete match.
MIN_MATCH_FRACTION = 0.9


def attach(cds_rows, genbank_path, phage_id=None):
    """Fill in `translation` on `cds_rows` in place. Returns a list of notes.

    Follows `pharokka.reconcile`: fill gaps, never silently overwrite what phold said,
    record every disagreement as a note the run output carries.
    """
    gb_rows, _meta = genbank_in.read(genbank_path, phage_id=phage_id)
    if not gb_rows:
        raise InputValidationError(
            "%s carries no CDS features with translations, so it cannot supply the "
            "protein sequences the phold TSV lacks" % genbank_path)

    by_span = {}
    for g in gb_rows:
        by_span.setdefault((g["start_nt"], g["end_nt"]), []).append(g)
    by_id = {}
    for g in gb_rows:
        for key in (g["cds_id"], g.get("raw_locus_tag")):
            if key:
                by_id.setdefault(str(key), g)

    notes, matched, id_conflicts = [], 0, 0
    for rec in cds_rows:
        span = (rec["start_nt"], rec["end_nt"])
        hits = by_span.get(span) or []
        if len(hits) > 1:
            notes.append("%s: %d GenBank CDS share the span %d-%d; left without a "
                         "sequence rather than guessing"
                         % (rec["cds_id"], len(hits), span[0], span[1]))
            continue
        g = hits[0] if hits else by_id.get(str(rec["cds_id"]))
        if g is None:
            notes.append("%s: no GenBank CDS at %d-%d and no id match; no sequence "
                         "attached" % (rec["cds_id"], span[0], span[1]))
            continue
        if not hits:
            notes.append("%s: matched by locus tag, not by coordinates (GenBank says "
                         "%d-%d, phold says %d-%d)"
                         % (rec["cds_id"], g["start_nt"], g["end_nt"], span[0], span[1]))
        elif str(g["cds_id"]) != str(rec["cds_id"]) and \
                str(g.get("raw_locus_tag") or "") != str(rec["cds_id"]):
            # The span is the stronger claim, so the sequence is attached -- but a
            # systematic id divergence usually means two different annotation runs.
            id_conflicts += 1
        if rec.get("translation") and rec["translation"] != g["translation"]:
            notes.append("%s: phold already carried a different sequence; kept phold's"
                         % rec["cds_id"])
            matched += 1
            continue
        rec["translation"] = g["translation"]
        matched += 1

    total = len(cds_rows)
    if total and matched / total < MIN_MATCH_FRACTION:
        raise InputValidationError(
            "only %d of %d CDS in the phold TSV could be matched to a CDS in %s "
            "(%.0f%%, and at least %.0f%% is required). These two files look like "
            "different annotation runs, and attaching a sequence to the wrong CDS "
            "would produce confident wrong structural evidence. Use the GenBank on "
            "its own instead: rbpfinder --genbank %s ..."
            % (matched, total, genbank_path, 100.0 * matched / max(total, 1),
               100.0 * MIN_MATCH_FRACTION, genbank_path))

    if id_conflicts:
        notes.append("%d CDS matched by coordinates while their identifiers differ "
                     "between the TSV and the GenBank; sequences were attached on the "
                     "coordinates. Check that both came from the same annotation run."
                     % id_conflicts)
    missing = [r["cds_id"] for r in cds_rows if not r.get("translation")]
    if missing:
        notes.append("%d of %d CDS have no protein sequence after matching, so they "
                     "carry no family T: %s"
                     % (len(missing), total, ", ".join(missing[:5])
                        + (" ..." if len(missing) > 5 else "")))
    return notes
