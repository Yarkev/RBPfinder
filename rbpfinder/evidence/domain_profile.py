"""Stage 2b -- family D, domain and profile evidence.

InterProScan returning zero matches is dark matter, not counter-evidence: all
three W073 tail-fibre proteins scored zero across every member database. A null
result becomes an `uninformative` record and can never lower a tier.

Unlike the homology table, both sources here carry a query span, so D records
are genuinely region-localised.
"""
from . import terms


def _full_region(rec):
    return {"start_aa": 1, "end_aa": max(rec["length_aa"], 1), "source": "manual"}


def from_interproscan(rec, hits, rules, provider_id):
    """Every qualifying hit becomes its OWN region-level record. Returns a LIST.

    M20-b2E4. This used to elect one representative hit with
    `_best = min(cands, key=RANK[category])` and take direction, region, strength and
    accession from it. Category ties were broken by the order InterProScan happened to
    emit its matches, so the reported region was decided by provider output order:
    M20-b2E1 measured 6 of 12 multi-hit artifacts reporting a different region under
    nothing but a reordering, and on P22 tailspike the two answers were 111-667 (the
    receptor-binding pectate-lyase beta-helix) and 7-109 (the N-terminal capsid-binding
    domain) -- opposite ends of the protein.

    No scalar ordering can fix that, because the domains describe different biological
    parts and none of them stands for the whole. So nothing is elected. Each record
    carries a single span, exactly as `evidence_schema.json` requires, and the candidate
    layer decides what the set of them means.

    De-correlation is unaffected: `evidence_families.decorrelate` still collapses many D
    records to at most one independent D. Keeping every region and not double-counting
    are separate questions.

    The pre-E4 implementation is frozen verbatim in `scripts/pre_e4_localisation.py`,
    where the shadow gates use it as their counter-example.
    """
    if not hits:
        return [{
            "family": "D", "tool": provider_id, "region": _full_region(rec),
            "direction": rules.get("stage2.interproscan.zero_hits_direction"),
            "strength": "weak",
            # "ran and found nothing" vs "never ran" are different states; only the
            # provider_id distinguishes them, so record it explicitly
            "not_run": provider_id == "domain_profile_absent",
            "raw": {"hit_description": "InterProScan returned zero matches "
                                       "(dark matter, not counter-evidence)"},
        }]

    out = []
    for h in hits:
        text = " ".join(filter(None, [h.get("entry_name"), h.get("entry_description"),
                                      h.get("name")]))
        cat, matched = terms.classify(text, rules)
        region = (_full_region(rec) if not h.get("start_aa") else
                  {"start_aa": h["start_aa"], "end_aa": h["end_aa"],
                   "source": "interpro_domain_span"})
        e = h.get("evalue")
        label = h.get("entry_name") or h.get("name") or h.get("accession")
        raw = {"hit_id": h.get("accession"),
               "hit_description": label + (" (%s)" % ",".join(matched)
                                           if matched else "")}
        # The schema types `raw.evalue` as a number, so a hit without one omits the key
        # rather than writing null -- absent and null are different states, and only
        # absent is valid here. Latent before E4: the pre-E4 reader wrote `"evalue": e`
        # unconditionally too, but emitted a single elected record, and the elected hit
        # usually carried an e-value. Giving every hit its own record makes InterPro
        # entries that have none (PANTHER/Gene3D family lines) reachable, and the schema
        # gate caught it on the first run.
        if e is not None:
            raw["evalue"] = e
        out.append({
            "family": "D", "tool": provider_id, "region": region,
            "direction": terms.direction_for(cat),
            "strength": "strong" if (e is not None and e <= 1e-10) else "moderate",
            "raw": raw,
        })
    return out


def from_hhpred(rec, hits, rules, provider_id):
    min_p = rules.get("stage2.hhpred.min_probability_pct")
    strong_p = rules.get("stage2.hhpred.strong_probability_pct")
    kept = [h for h in hits if (h.get("probability_pct") or 0) >= min_p]

    if not kept:
        best_seen = hits[0]["probability_pct"] if hits else None
        return {
            "family": "D", "tool": provider_id, "region": _full_region(rec),
            "direction": "uninformative", "strength": "weak",
            "raw": {"hit_description":
                    "no HHpred hit at or above %s%% probability (best seen: %s)"
                    % (min_p, best_seen)},
        }

    cands = []
    for h in kept:
        c, matched = terms.classify(h["description"], rules)
        cands.append((c, h, matched))
    cat = min((c[0] for c in cands), key=lambda k: terms.RANK.get(k, 9))
    pick = max((c for c in cands if c[0] == cat), key=lambda c: c[1]["probability_pct"])
    _, h, matched = pick

    qs = h.get("query_span")
    region = ({"start_aa": qs[0], "end_aa": qs[1], "source": "hhpred_alignment_span"}
              if qs else _full_region(rec))
    return {
        "family": "D", "tool": provider_id, "region": region,
        "direction": terms.direction_for(cat),
        "strength": "strong" if h["probability_pct"] >= strong_p else "moderate",
        "raw": {"hit_id": h["hit_id"], "hit_description": h["description"],
                "probability_pct": h["probability_pct"], "evalue": h.get("evalue")},
    }
