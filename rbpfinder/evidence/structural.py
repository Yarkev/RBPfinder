"""Stage 3 -- family T, structural evidence.

Two rules shape everything here.

First, a structural hit to something merely NAMED a tail fibre proves apparatus
membership, not receptor binding. Stage 2 was already forced to learn this: on
W073 the Ig adapter rod, the real RBP, the connector and the tip all have
tail-fibre-named relatives. Functional identity comes from characterised
databases (PDB, CATH) whose entries carry a descriptive title.

Second, AF3 geometry is a shape heuristic. Elongated-and-trimeric describes a
collagen fibre and an Ig rod as well as it describes an RBP, so geometry may
support a fibrous role but may never, on its own, mean receptor binding.
"""
from . import terms


def _full_region(rec):
    return {"start_aa": 1, "end_aa": max(rec["length_aa"], 1), "source": "manual"}


def _span(h, rec):
    qs, qe = h.get("query_span") or (None, None)
    if qs and qe:
        return {"start_aa": int(qs), "end_aa": int(qe), "source": "foldseek_hit_span"}
    return _full_region(rec)


def consensus(hits, rules):
    """Summarise the functional-database hits, keeping the whole distribution."""
    func_dbs = set(rules.get("stage3.functional_databases"))
    top_n = rules.get("stage3.hit_consensus.top_n")
    min_p = rules.get("stage3.hit_consensus.min_probability")
    max_e = rules.get("stage3.hit_consensus.max_evalue")

    kept = [h for h in hits
            if h["database"] in func_dbs
            and (h["probability"] is None or h["probability"] >= min_p)
            and (h["evalue"] is None or h["evalue"] <= max_e)][:top_n]

    counts, per_hit = {}, []
    for h in kept:
        cat, matched = terms.classify(h["description"], rules)
        counts[cat] = counts.get(cat, 0) + 1
        per_hit.append({
            "database": h["database"], "target": h["target"],
            "description": h["description"][:120], "probability": h["probability"],
            "evalue": h["evalue"], "query_span": h["query_span"],
            "category": cat, "matched_terms": matched,
        })
    informative = sum(v for k, v in counts.items() if k != terms.UNINFORMATIVE)
    frac = (counts.get(terms.POSITIVE, 0) / informative) if informative else 0.0
    return {"n_considered": len(kept), "counts": counts, "per_hit": per_hit,
            "positive_fraction": frac, "informative": informative,
            "n_functional_hits": sum(1 for h in hits if h["database"] in func_dbs)}


def build(rec, hits, rules, provider_id):
    """One T-family record plus the consensus table. Absence is stated, not omitted."""
    if not hits:
        return {
            "family": "T", "tool": provider_id, "region": _full_region(rec),
            "direction": "uninformative", "strength": "weak",
            "not_run": True,
            "raw": {"hit_description": "no structural search result available"},
        }, None

    cons = consensus(hits, rules)
    if not cons["per_hit"]:
        # hits exist, but only against uncharacterised model databases
        apparatus_dbs = ", ".join(sorted({h["database"] for h in hits}))
        return {
            "family": "T", "tool": provider_id, "region": _full_region(rec),
            "direction": "uninformative", "strength": "weak",
            "raw": {"hit_description":
                    "structural hits only in uncharacterised model databases (%s); "
                    "no functional identity available" % apparatus_dbs},
        }, cons

    frac_mod = rules.get("stage2.strength_rules.consensus_fraction_moderate")
    counts = cons["counts"]
    if counts.get(terms.POSITIVE, 0) and cons["positive_fraction"] >= frac_mod:
        cat = terms.POSITIVE
    elif cons["informative"]:
        cat = min((k for k in counts if k != terms.UNINFORMATIVE),
                  key=lambda k: (terms.RANK.get(k, 9), -counts[k]))
        if cat == terms.NOISE:
            cat = terms.UNINFORMATIVE
    else:
        cat = terms.UNINFORMATIVE

    # localise to the hits that actually carry the winning category
    matching = [h for h in cons["per_hit"] if h["category"] == cat] or cons["per_hit"]
    spans = [h["query_span"] for h in matching if h["query_span"] and h["query_span"][0]]
    if spans:
        region = {"start_aa": int(min(s[0] for s in spans)),
                  "end_aa": int(max(s[1] for s in spans)),
                  "source": "foldseek_hit_span"}
    else:
        region = _full_region(rec)

    best = matching[0]
    strong = (cat == terms.POSITIVE and counts.get(terms.POSITIVE, 0) >= 3)
    return {
        "family": "T", "tool": provider_id, "region": region,
        "direction": terms.direction_for(cat),
        "strength": "strong" if strong else "moderate",
        "raw": {
            "hit_id": best["target"],
            "hit_description": "%s | top-%d functional consensus: %s"
                               % (best["description"][:70], cons["n_considered"],
                                  ", ".join("%s=%d" % (k, v)
                                            for k, v in sorted(counts.items()))),
            "evalue": best["evalue"],
            "probability_pct": (best["probability"] * 100
                                if best["probability"] is not None else None),
        },
    }, cons


def geometry_record(rec, geom, rules, provider_id):
    """AF3 shape heuristic. Explicitly flagged, and never receptor-binding."""
    if not geom:
        return None
    rg = geom.get("rg_ratio")
    thr = rules.get("stage3.geometry.rg_ratio_fibrous")
    shape = "fibrous" if (rg is not None and rg >= thr) else "compact"
    return {
        "family": "T", "tool": provider_id, "region": _full_region(rec),
        # geometry can describe a shape; it may never assert receptor binding
        "direction": "supports_structural_only" if shape == "fibrous" else "uninformative",
        "strength": "weak",
        "is_heuristic": True,
        "raw": {"hit_description": "AF3 geometry: %s (Rg ratio %s, mean pLDDT %s) -- "
                                   "uncalibrated shape heuristic"
                                   % (shape, rg, geom.get("mean_plddt"))},
    }
