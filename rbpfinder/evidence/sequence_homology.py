"""Stage 2a -- family S, sequence homology.

The useful signal is rarely the top hit's own name. It is the CONSENSUS of the
first several homologs' functional annotations: "top hit = hypothetical protein"
hides that six of the next nine are annotated tail fibre. So the whole top-N
distribution is computed, kept and reported, not just the best row.
"""
from . import terms


def _full_region(rec):
    return {"start_aa": 1, "end_aa": max(rec["length_aa"], 1), "source": "manual"}


def consensus(hits, rules):
    top_n = rules.get("stage2.homolog_consensus.top_n")
    max_e = rules.get("stage2.homolog_consensus.max_evalue")

    kept = [h for h in hits if h.get("evalue") is None or h["evalue"] <= max_e][:top_n]
    counts, per_hit = {}, []
    for h in kept:
        cat, matched = terms.classify(h["description"], rules)
        counts[cat] = counts.get(cat, 0) + 1
        per_hit.append({
            "rank": h["rank"], "accession": h["accession"],
            "query_span": h.get("query_span"),
            "description": h["description"], "organism": h.get("organism", ""),
            "identity_pct": h.get("identity_pct"), "evalue": h.get("evalue"),
            "category": cat, "matched_terms": matched,
        })

    informative = sum(v for k, v in counts.items() if k != terms.UNINFORMATIVE)
    frac_pos = (counts.get(terms.POSITIVE, 0) / informative) if informative else 0.0
    best_e = min((h["evalue"] for h in kept if h.get("evalue") is not None), default=None)
    return {"n_considered": len(kept), "counts": counts, "per_hit": per_hit,
            "positive_fraction": frac_pos, "best_evalue": best_e,
            "informative": informative}


def _region(rec, cons, cat):
    """Localise to the hits carrying the winning category, when spans exist."""
    matching = [h for h in cons["per_hit"] if h["category"] == cat] or cons["per_hit"]
    spans = [h.get("query_span") for h in matching
             if h.get("query_span") and h["query_span"][0] and h["query_span"][1]]
    if not spans:
        return _full_region(rec)
    return {"start_aa": int(min(s[0] for s in spans)),
            "end_aa": int(max(s[1] for s in spans)),
            "source": "blast_alignment_span"}


def _strength(cons, rules):
    b = "stage2.strength_rules"
    e = cons["best_evalue"]
    if (cons["positive_fraction"] >= rules.get(b + ".consensus_fraction_strong")
            and e is not None and e <= rules.get(b + ".evalue_strong")):
        return "strong"
    if (cons["positive_fraction"] >= rules.get(b + ".consensus_fraction_moderate")
            or (e is not None and e <= rules.get(b + ".evalue_moderate"))):
        return "moderate"
    return "weak"


def _category(cons, rules):
    counts = cons["counts"]
    if cons["informative"] == 0:
        return terms.UNINFORMATIVE
    frac_moderate = rules.get("stage2.strength_rules.consensus_fraction_moderate")
    if counts.get(terms.POSITIVE, 0) and cons["positive_fraction"] >= frac_moderate:
        return terms.POSITIVE
    cat = min((k for k in counts if k != terms.UNINFORMATIVE),
              key=lambda k: (terms.RANK.get(k, 9), -counts[k]))
    # a consensus made only of high-noise words carries no direction
    return terms.UNINFORMATIVE if cat == terms.NOISE else cat


def build(rec, hits, rules, provider_id, searched=False):
    """Return one S-family record plus the consensus table.

    Absence of a result is recorded explicitly as `uninformative`, never omitted:
    a missing provider must not look like a negative finding.

    An empty result has TWO meanings and they are not interchangeable:

        searched=False   no provider ran            -> not_run=True
        searched=True    a search ran, zero hits    -> not_run=False

    af3_priority._ran() turns on exactly this bit. Collapsing the two -- which is
    what this function did until 2026-08-23 -- makes every zero-hit candidate look
    unsearched, so STAGE2_FIRST keeps firing no matter how many databases were
    consulted and the AF3 P1 list can never mean "sequence evidence is exhausted".
    W073cp2a2_CDS_0003 is the case that showed it: a 0-byte BLAST artefact on disk,
    an S record claiming the provider was absent.

    Both remain `uninformative` in direction. Per absence_is_not_counter_evidence,
    a search that found nothing must never lower a tier.
    """
    if not hits:
        return {
            "family": "S", "tool": provider_id, "region": _full_region(rec),
            "direction": "uninformative", "strength": "weak",
            "not_run": not searched,
            "raw": {"hit_description":
                    "sequence search ran and returned no hits" if searched
                    else "no sequence-homology result available"},
        }, None

    cons = consensus(hits, rules)

    # A THIRD empty state, and it crashed the executor before it was named (P0, found in
    # real-machine testing 2026-08-25). The provider returned hits -- `hits` is non-empty,
    # so the branch above does not fire -- but every one of them failed
    # `stage2.homolog_consensus.max_evalue`, leaving `per_hit` empty and
    # `cons["per_hit"][0]` raising IndexError.
    #
    # The search DID run and it DID return rows. What it did not return is anything that
    # clears the evidence threshold. That is `searched, no qualifying hit`: not_run stays
    # False, direction stays uninformative, and the tier is not lowered -- filtering
    # something out is not evidence against it.
    if not cons["per_hit"]:
        return {
            "family": "S", "tool": provider_id, "region": _full_region(rec),
            "direction": "uninformative", "strength": "weak",
            "not_run": False,
            "raw": {
                "hit_description": "search ran; all hits failed the evidence threshold",
                "raw_hit_count": len(hits),
                "retained_hit_count": 0,
                "filter_max_evalue": rules.get("stage2.homolog_consensus.max_evalue"),
            },
        }, cons

    cat = _category(cons, rules)
    top = cons["per_hit"][0]
    return {
        "family": "S", "tool": provider_id,
        # localised when the provider reports a query span. The older EBI tables carry
        # none, so those records stay full-length and say so via region.source=manual.
        "region": _region(rec, cons, cat),
        "direction": terms.direction_for(cat),
        "strength": _strength(cons, rules),
        "raw": {
            "hit_id": top["accession"],
            "hit_description": "%s | top-%d consensus: %s"
                               % (top["description"], cons["n_considered"],
                                  ", ".join("%s=%d" % (k, v)
                                            for k, v in sorted(cons["counts"].items()))),
            "evalue": cons["best_evalue"],
            "identity_pct": top["identity_pct"],
        },
    }, cons
