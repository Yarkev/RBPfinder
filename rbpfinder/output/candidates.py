"""Candidate ranking and set assignment. Ordering is total and deterministic."""

_TIER_R = ["R1", "R2", "R3", "N"]
_TIER_E = ["E3", "E2", "E1"]


def sort_key(cand, rules):
    order = rules.get("stage7.ranking.order_by")
    key = []
    for spec in order:
        k = spec["key"]
        if k == "tier_r":
            key.append(_TIER_R.index(cand["tier_r"]))
        elif k == "tier_e":
            key.append(_TIER_E.index(cand["tier_e"]))
        elif k == "independent_family_count":
            key.append(-len(cand.get("independent_families", [])))
        elif k == "receptor_binding_family_count":
            key.append(-len(cand["_rb_families"]))
        elif k == "max_receptor_binding_strength":
            key.append(cand["_max_rb_strength_rank"])
        elif k == "cds_id":
            key.append(cand["cds_id"])
        else:
            raise KeyError("unknown ranking key %r" % k)
    return tuple(key)


def rank(candidates, rules):
    return sorted(candidates, key=lambda c: sort_key(c, rules))


_RB = "supports_receptor_binding"


def direct_binding_hint(cand, rules):
    """Does this candidate carry evidence that actually points at receptor binding?

    Consumes the normalised EvidenceRecords rather than re-reading product text. A
    second RBP-term vocabulary living here would drift from stage4.direction_map within
    one edit, and the two would then disagree silently.

    G is deliberately not an allowed family: genomic context says a protein is part of
    the apparatus, never that it binds anything. Same for a C5 comparative record, which
    the YAML locks at apparatus membership -- admitting it here would re-import C5 as
    RBP evidence through the back door of set assignment.
    """
    fams = set(rules.get("rescue_policy.direct_binding_hint.allowed_families"))
    for reg in cand.get("regions", []):
        for ev in reg.get("evidence", []):
            if (ev.get("direction") == _RB
                    and ev.get("family") in fams
                    and not ev.get("superseded_by")):
                return True, {"family": ev["family"], "tool": ev["tool"],
                              "strength": ev.get("strength")}
    return False, None


def assign_sets(ranked, rules):
    """Primary / rescue / internal-pool-only.

    Rescue is NOT "the rest of the pool". It is `remaining R2, plus R3 carrying a
    direct-binding hint`, exactly as stage7.candidate_set.rescue_set has always said.
    Every other recall-risk signal -- an audit reopening, exhausted sequence evidence, a
    C5 variable locus -- is a reason the candidate space could not be safely compressed,
    and it belongs in the completeness limitations. "I do not know what this protein is"
    is not "worth testing whether it is an RBP".

    typical_size is an expected distribution, never a cap: five qualifying candidates
    are reported as five, and zero as zero.
    """
    lo, hi = rules.get("stage7.candidate_set.primary_set.typical_size")
    primary, rescue, reported = [], [], []
    for cand in ranked:
        cid = cand["cds_id"]
        if cand["tier_r"] == "N":
            reported.append(cid)
            continue
        if cand["tier_r"] in ("R1", "R2") and len(primary) < hi:
            primary.append(cid)
            continue
        if cand["tier_r"] == "R2":
            rescue.append(cid)                       # remaining R2
            continue
        if cand["tier_r"] == "R3":
            hint, detail = direct_binding_hint(cand, rules)
            if hint:
                cand["rescue_reason"] = detail
                rescue.append(cid)
                continue
        reported.append(cid)
    for cand in ranked:
        cid = cand["cds_id"]
        cand["set_membership"] = (
            "primary" if cid in primary else "rescue" if cid in rescue else "reported_only"
        )
    return primary, rescue


def write_tsv(ranked, path):
    cols = ["rank", "cds_id", "tier", "protein_role", "definition_layer",
            "nomination_channels", "independent_families", "region_start_aa",
            "region_end_aa", "region_role", "rbp_evidence_localisation",
            "af3_priority", "set_membership", "annotation"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(cols) + "\n")
        for i, c in enumerate(ranked, 1):
            for reg in c["regions"]:
                row = {
                    "rank": i,
                    "cds_id": c["cds_id"],
                    "tier": "%s-%s" % (c["tier_r"], c["tier_e"]),
                    "protein_role": c["protein_role"],
                    "definition_layer": c["definition_layer"],
                    "nomination_channels": ",".join(c["nomination_channels"]),
                    "independent_families": ",".join(c.get("independent_families", [])),
                    "region_start_aa": reg["region"]["start_aa"],
                    "region_end_aa": reg["region"]["end_aa"],
                    "region_role": reg["role"],
                    "rbp_evidence_localisation": c.get("rbp_evidence_localisation") or "",
                    "af3_priority": c.get("af3_priority", ""),
                    "set_membership": c.get("set_membership", ""),
                    "annotation": c["annotation"]["product"],
                }
                fh.write("\t".join(str(row[k]) for k in cols) + "\n")
