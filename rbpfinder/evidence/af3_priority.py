"""Stage 3b -- which candidates would gain most from an AF3 structure.

The criterion is information gain, not residual doubt. A "run it only if still
in doubt" rule ranks lowest exactly the case that needs structure most: a novel
RBP with no BLAST, no InterPro and no HHpred result. decision_rules.yaml records
that rule as forbidden by name.

Nothing here submits anything. It produces a request list for the
AF3_MONOMER_BATCH human breakpoint.
"""
import hashlib

_RB = "supports_receptor_binding"
_NON_RBP = "supports_non_rbp_identity"
_APPARATUS = ("supports_apparatus_membership", "supports_structural_only")


def _informative(records, family):
    return [r for r in records
            if r["family"] == family and r["direction"] != "uninformative"]


def _ran(records, family):
    """Did a provider for this family actually run?

    "Searched and found nothing" and "never searched" are different states, and
    the project invariant absence_is_not_counter_evidence turns on the
    difference. Only the first is an information-gain signal for AF3: spending a
    structure prediction on a protein nobody has even BLASTed gets the cost
    order backwards -- the cheap search comes first.
    """
    return any(r["family"] == family and not r.get("not_run") for r in records)


def assign(rec, cand, records, rules):
    """Return (priority, trigger_id, reason)."""
    base = "stage3.af3_priority"
    chans = set(cand.get("nomination_channels") or [])
    counted = [r for r in records if r.get("counts_as_independent")]

    has_s = bool(_informative(records, "S"))
    has_d = bool(_informative(records, "D"))
    has_g = bool(_informative(records, "G"))
    ran_s, ran_d = _ran(records, "S"), _ran(records, "D")
    rb = [r for r in counted if r["direction"] == _RB]
    non_rbp = [r for r in counted if r["direction"] == _NON_RBP]

    # --- P3 first: a resolved role gains nothing from another model
    if cand["tier_r"] == "N":
        return "P3", "positive_non_rbp_identity", "annotator gives a positive non-RBP identity"
    localised = any(r["region"].get("source") in
                    ("foldseek_hit_span", "hhpred_alignment_span", "interpro_domain_span")
                    for r in rb)
    if cand["tier_r"] == "R1" and localised:
        return "P3", "role_already_resolved", "R1 with a localised receptor-binding region"

    # --- P1: mandatory
    if rb and non_rbp:
        return ("P1", "evidence_conflict",
                "one line supports receptor binding while another asserts a non-RBP identity; "
                "structure is the arbiter")
    if not ran_s and not ran_d:
        return ("STAGE2_FIRST", "sequence_evidence_not_yet_run",
                "no sequence or profile search has been run for this candidate; the cheap "
                "search comes before a structure prediction")
    if has_g and not has_s and not has_d:
        return ("P1", "context_strong_sequence_empty",
                "genomic context places it in the apparatus, and sequence and profile "
                "searches RAN and returned nothing informative")
    if ("C3" in chans or "C4" in chans) and not has_d and ran_d:
        return ("P1", "dark_matter_in_rbp_cluster",
                "unannotated CDS inside a nominated tail cluster, profile search empty")
    if "C5" in chans:
        return "P1", "variable_locus", "sits in a comparative-genomics variable locus"
    if "C6" in chans and not has_d:
        return ("P1", "ml_nominated_unexplained",
                "machine-learning nomination that the annotation cannot explain")
    min_aa = rules.get(base + ".P1_mandatory.modular_fibre_min_aa")
    if rec["length_aa"] >= min_aa and any(r["direction"] in _APPARATUS for r in counted):
        return ("P1", "modular_tail_fibre_length",
                "%d aa apparatus protein -- long enough to hide a terminal RBD"
                % rec["length_aa"])

    # --- P2: recommended
    if rb and not localised:
        return ("P2", "localise_rbd",
                "receptor-binding evidence exists but is not localised to a sub-region")
    if cand["tier_r"] in ("R2", "R3") and any(r["direction"] in _APPARATUS for r in counted):
        return ("P2", "separate_rbp_from_adapter",
                "apparatus evidence only; structure would separate RBP from adapter")
    k = cand.get("_k_default")
    if k and cand.get("_rank") and cand["_rank"] <= k and cand["tier_r"] != "R1":
        return "P2", "primary_set_decision", "inside k_default but not yet R1"

    return "P3", "no_information_gain", "no trigger matched"


def manifest_rows(ranked, by_id, records_by_id, rules, k_default):
    fields = rules.get("stage3.af3_priority.batch.manifest_fields")
    rows = []
    for i, cand in enumerate(ranked, 1):
        cand["_rank"], cand["_k_default"] = i, k_default
        rec = by_id[cand["cds_id"]]
        prio, trigger, reason = assign(rec, cand, records_by_id[cand["cds_id"]], rules)
        cand["af3_priority"] = prio
        seq = rec.get("translation") or ""
        rows.append({
            "candidate_id": cand["cds_id"],
            "priority": prio,
            "trigger": trigger,
            "sequence_sha256": hashlib.sha256(seq.encode()).hexdigest() if seq else "",
            "length_aa": rec["length_aa"],
            "requested_reason": reason,
        })
    return fields, rows
