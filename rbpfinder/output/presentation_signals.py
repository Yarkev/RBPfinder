"""Hints for a human reader. Never evidence, never a tier, never a family.

A Biomni test round found a false positive no evidence family could have caught: 01F2
gp16, product name "tail fiber protein", pushed into Primary by a 99.3%-identity homolog
*also* called "tail fiber protein". A cryo-EM map showed a tube initiator -- baseplate
assembly machinery. Agreement between two readers of the same PART NAME is not two
independent lines of evidence about function, but de-correlation collapses tools rather
than vocabularies, so both records stood and nothing in the output said why to doubt them.

This module surfaces that shape. Three checks, all read-only:

    structure_annotation_conflict   a hit DESCRIPTION describes assembly machinery
                                    while the product name claims a fibre
    fibre_chaperone_adjacency       a fibre assembly chaperone sits immediately
                                    downstream, same strand -- fibre operon corroboration
    direction_conflict              the candidate's own records disagree about direction

Everything here is **presentation only**, and that is enforced rather than promised:
`tests/test_presentation_signals.py` fills the whole rules block with an adversarial
vocabulary -- every term matching everything -- and asserts the pool, the families, the
tiers and Primary/Rescue do not move. Nothing in this file mutates its inputs.

`term_trigger_counts` is in the output because the vocabulary is `calibration: pending`:
it is a starting point from one measured case, with no fitted trigger rate. The counts
are how that gets fixed -- run it, see which terms fire and how often, prune. `complex` is
the term to watch, since PDB titles say it constantly.
"""

_BASE = "presentation_signals"


def _terms(rules, dotted, default=()):
    v = rules.get("%s.%s" % (_BASE, dotted), None)
    return [str(x).lower() for x in (v if v else default)]


def _enabled(rules, check):
    return bool(rules.get("%s.%s.enabled" % (_BASE, check), False))


def _names_another_part(product, rules):
    """True when this CDS's own product positively names a different structural part.

    Both signals are about a *fibre*, so neither should fire for a protein the annotator
    already called something else. Two measured looseness reports collapse into this one
    rule:

      * a chaperone fired the conflict check for its own hit titles, because
        "tail fiber assembly protein" CONTAINS the substring "tail fiber". That is the
        same bug class as the C1/C2 whole-token fix and `normalize._direction`'s
        "a compound name takes the conservative reading" -- a rule this codebase already
        had, which this matcher failed to carry over when it was written.
      * the adjacency signal fired on a tail sheath. A chaperone next to a sheath does
        not make the sheath a fibre.

    The term list is a POINTER to `stage4.direction_map.supports_structural_only`, not a
    copy, so there is one vocabulary to fix. "tail fiber protein" is not on it and stays
    in scope; the helper nouns, sheath, tube and baseplate are on it and are skipped.
    """
    if not rules.get(_BASE + ".self_product_names_another_part.enabled", False):
        return False
    src = rules.get(_BASE + ".self_product_names_another_part.terms_from")
    terms = [str(x).lower() for x in (rules.get(src) or ())] if src else []
    low = (product or "").lower()
    return any(term in low for term in terms)


def _hit_descriptions(records):
    """Every hit description a candidate carries, with the family that produced it."""
    out = []
    for r in records or ():
        text = ((r.get("raw") or {}).get("hit_description") or "").strip()
        if text:
            out.append((r.get("family"), r.get("tool"), text))
    return out


def _structure_annotation_conflict(product, records, rules, counts):
    part = _terms(rules, "structure_annotation_consistency.part_name_terms")
    ctx = _terms(rules, "structure_annotation_consistency.assembly_context_terms")
    low = (product or "").lower()
    claimed = [p for p in part if p in low]
    if not claimed or _names_another_part(product, rules):
        return None

    found = []
    for family, tool, text in _hit_descriptions(records):
        t = text.lower()
        for term in ctx:
            if term in t:
                counts[term] = counts.get(term, 0) + 1
                found.append({"family": family, "tool": tool, "term": term,
                              "hit_description": text})
    if not found:
        return None
    return {
        "product_claims": sorted(set(claimed)),
        "but_hits_describe": found,
        "reading": "the product name claims a fibre while a hit description describes "
                   "assembly machinery. Two readers agreeing on a part name is not two "
                   "independent lines of evidence about function.",
        "action": "worth a human look; no tier was changed",
    }


def _fibre_chaperone_adjacency(cds_id, cds_rows, rules, is_circular, counts,
                               product=None):
    """A fibre assembly chaperone immediately downstream, on the same strand.

    Downstream follows the strand, because the signal is an operon one: a fibre and its
    chaperone are co-transcribed, so for a reverse-strand gene the chaperone is at LOWER
    coordinates. Taking "downstream" to mean "higher coordinate" would silently miss
    every reverse-strand fibre.
    """
    terms = _terms(rules, "fibre_chaperone_adjacency.chaperone_terms_subset")
    window = rules.get("%s.fibre_chaperone_adjacency.downstream_window_cds" % _BASE, 2)
    if not terms or _names_another_part(product, rules):
        return None

    n = len(cds_rows)
    idx = next((i for i, r in enumerate(cds_rows) if r["cds_id"] == cds_id), None)
    if idx is None or n == 0:
        return None
    step = -1 if str(cds_rows[idx].get("strand")) in ("-", "-1", "reverse") else 1

    found = []
    for k in range(1, int(window) + 1):
        j = idx + step * k
        if is_circular:
            j %= n
        elif not 0 <= j < n:
            break
        nb = cds_rows[j]
        if str(nb.get("strand")) != str(cds_rows[idx].get("strand")):
            continue                      # a different operon, not this fibre's chaperone
        low = (nb.get("product") or "").lower()
        for term in terms:
            if term in low:
                counts[term] = counts.get(term, 0) + 1
                found.append({"cds_id": nb["cds_id"], "offset": step * k,
                              "product": nb.get("product"), "term": term})
                break
    if not found:
        return None
    return {
        "chaperones_downstream": found,
        "reading": "a fibre assembly chaperone sits immediately downstream on the same "
                   "strand, which is how fibre operons are usually arranged",
        "action": "corroboration for a reader. NOT a nomination channel and NOT a "
                  "family: the C1 positional evidence for this CDS is already an "
                  "independent family and must not be counted twice",
    }


def _direction_conflict(records, rules):
    rb = str(rules.get("%s.direction_conflict.receptor_binding_direction" % _BASE,
                       "supports_receptor_binding"))
    against = {str(x) for x in
               (rules.get("%s.direction_conflict.conflicting_directions" % _BASE) or ())}
    seen = {}
    for r in records or ():
        seen.setdefault(str(r.get("direction")), []).append(
            {"family": r.get("family"), "tool": r.get("tool"),
             "strength": r.get("strength")})
    if rb not in seen:
        return None
    clash = sorted(d for d in seen if d in against)
    if not clash:
        return None
    return {
        "receptor_binding_from": seen[rb],
        "contradicted_by": {d: seen[d] for d in clash},
        "reading": "this candidate's own records disagree about direction. One "
                   "representative per family cannot carry both readings, so the "
                   "disagreement is invisible in the family list",
        "action": "worth a human look; no tier was changed",
    }


def build(candidates, records_by_id, cds_rows, rules, is_circular=False):
    """Return the `presentation_signals` block for one run.

    Reads only. Returns a fresh structure and mutates nothing it was given, so that a
    caller cannot accidentally let a hint reach the evidence chain.
    """
    counts = {}
    signals = {}
    for cand in candidates or ():
        cid = cand.get("cds_id")
        records = (records_by_id or {}).get(cid) or []
        product = ((cand.get("annotation") or {}).get("product")
                   or next((r.get("product") for r in cds_rows
                            if r["cds_id"] == cid), None))
        per = {}
        if _enabled(rules, "structure_annotation_consistency"):
            hit = _structure_annotation_conflict(product, records, rules, counts)
            if hit:
                per["structure_annotation_conflict"] = hit
        if _enabled(rules, "fibre_chaperone_adjacency"):
            hit = _fibre_chaperone_adjacency(cid, cds_rows, rules, is_circular, counts,
                                             product=product)
            if hit:
                per["fibre_chaperone_adjacency"] = hit
        if _enabled(rules, "direction_conflict"):
            hit = _direction_conflict(records, rules)
            if hit:
                per["direction_conflict"] = hit
        if per:
            signals[cid] = per

    return {
        "layer": rules.get("%s.layer" % _BASE, "presentation_only"),
        "calibration": rules.get("%s.calibration" % _BASE, "pending"),
        "disclaimer": "Hints for a human reader. No signal here changed a tier, a "
                      "family, a nomination or a candidate set, and none may be "
                      "reported as evidence that a protein is or is not an RBP.",
        "candidates_examined": len(candidates or ()),
        "candidates_with_any_signal": len(signals),
        # calibration: pending, so the trigger rate has to be observable before the
        # word lists can be trusted or pruned.
        "term_trigger_counts": dict(sorted(counts.items())),
        "signals": signals,
    }
