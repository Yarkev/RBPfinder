"""Rule-based two-axis tiering. No weighted score -- decision_rules.yaml
forbids one until a calibration dataset exists.
"""
from .evidence_families import counted

_RB = "supports_receptor_binding"
_NON_RBP = "supports_non_rbp_identity"
_STRUCT = "supports_structural_only"
_APPARATUS = "supports_apparatus_membership"
_STRENGTH_ORDER = {"strong": 0, "moderate": 1, "weak": 2}


def assign(records, independent_families, rules):
    c = counted(records)
    rb_families = sorted({r["family"] for r in c if r["direction"] == _RB})
    direct_families = set(rb_families) & {"T", "D", "S"}
    hard_contradiction = any(r["direction"] == _NON_RBP for r in c)
    apparatus = any(r["direction"] in (_APPARATUS, _STRUCT) for r in c)

    if hard_contradiction:
        tier_r = "N"
    elif len(rb_families) >= 2 and direct_families and rb_families != ["G"]:
        tier_r = "R1"
    elif len(independent_families) >= 2 and rb_families:
        tier_r = "R2"
    elif apparatus or independent_families:
        tier_r = "R3"
    else:
        tier_r = "R3"

    n = len(independent_families)
    tier_e = "E3" if n >= 3 else ("E2" if n == 2 else "E1")

    rb_strength = min(
        (_STRENGTH_ORDER.get(r["strength"], 9) for r in c if r["direction"] == _RB),
        default=9,
    )
    return {
        "tier_r": tier_r,
        "tier_e": tier_e,
        "rb_families": rb_families,
        "hard_contradiction": hard_contradiction,
        "max_rb_strength_rank": rb_strength,
    }


def confirmed_rbp(records, tier):
    """M22-a. Rule-based, no score -- SHADOW until adopted (`confirmed_rbp_designation`).

    Narrower than R1. R1 already requires >=2 RB families with >=1 direct (T/D/S) at ANY
    strength -- a single strong hit plus a weak second family qualifies. This asks for
    more: at least two independent DIRECT families each individually reaching `strong`.

    `counted(records)` already does the work: `evidence_families.decorrelate` marks
    exactly one record per independent family (the best-strength one), so grouping the
    counted set by family and checking `strength` directly is enough -- no new per-family
    tracking is needed alongside `assign()`.
    """
    if tier["tier_r"] != "R1":
        return False
    c = counted(records)
    strong_direct = {r["family"] for r in c
                     if r["direction"] == _RB and r["family"] in {"T", "D", "S"}
                     and r.get("strength") == "strong"}
    return len(strong_direct) >= 2


def role_and_layer(tier, records, rules):
    c = counted(records)
    products = " ".join(
        ((r.get("raw") or {}).get("hit_description") or "") for r in c
    ).lower()
    depoly_terms = ["depolymerase", "sialidase", "glycoside", "glycosyl", "hydrolase"]

    if tier["tier_r"] == "N":
        return "non_rbp", "non_rbp"
    if tier["tier_r"] == "R1":
        role = "depolymerase_rbp" if any(t in products for t in depoly_terms) else "main_rbp"
        return role, "RBP_core"
    if tier["tier_r"] == "R2":
        return "unresolved", "RBP_associated"
    if any(r["direction"] == "supports_structural_only" for r in c):
        return "connector_baseplate", "structural_tail_component"
    return "unresolved", "RBP_associated"


def rb_localisation_spans(records):
    """The canonical localisation: every region carrying receptor-binding evidence.

    M20-b2E4, and two changes from what stood here before.

    It reads every record with the receptor-binding direction, NOT `counted(records)`.
    `counts_as_independent` answers "how many independent families support this
    candidate" -- a de-correlation question. Where the evidence sits is a different
    question, and using the same single marked record for both was the representative-hit
    conflation one layer down: `decorrelate` picks its per-family record with a STABLE
    sort on (strength, tool), so equal-strength records from one tool fell back to input
    order and the reported region moved with it.

    It returns a LIST, not a hull. The hull it used to report, min(start)..max(end),
    asserts that everything between the outermost hits is evidence; nothing supports the
    gaps, and on a multi-domain tail protein the gaps are where the other domains are.
    Measured on the InterPro corpus, that hull covered >=90% of the protein on 4 of 12
    multi-hit artifacts -- P22 tailspike collapsing to 1-667 of 667 aa.
    """
    spans = sorted({(r["region"]["start_aa"], r["region"]["end_aa"])
                    for r in records
                    if r["direction"] == _RB and r.get("region")})
    return [{"start_aa": a, "end_aa": b} for a, b in spans]


def rb_localisation(records):
    """Residue ranges carrying the receptor-binding evidence. Required for RBP_core.

    The display rendering of `rb_localisation_spans`. Still a string, so the schema's
    `rbp_evidence_localisation` is unchanged -- but it now lists the real spans instead
    of a hull over them. A hull may be shown as a derived summary; it is not this.
    """
    spans = rb_localisation_spans(records)
    if not spans:
        return None
    return "aa " + ", ".join("%d-%d" % (s["start_aa"], s["end_aa"]) for s in spans)
