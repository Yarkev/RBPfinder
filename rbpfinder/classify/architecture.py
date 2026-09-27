"""Stage 0 -- automatic architecture typing as a SOFT PRIOR.

What this decides: which loci to prioritise and which k_default to display.
What it must never decide: whether a CDS can be nominated. The rules forbid
`exclude_candidate_based_on_architecture` and `cap_window_count_based_on_architecture`,
so a wrong prior costs ranking quality, never reachability. That is also why M11 is
graded on recall under auto-typing rather than on typing accuracy.

Signature satisfaction with conflict resolution -- no weighted score. The project has
no weighted totals anywhere; a hidden one at Stage 0 would be the easiest place to
smuggle in an unexplainable decision.

Three shapes of honesty carried over from the rest of the system:

  * absence is not positive evidence. "No sheath" cannot make something a siphovirus;
    it can only break a tie between classes whose own signatures already fired.
  * a class is never inferred by elimination. Missing the N4 vRNAP marker means E,
    never "it must be D then".
  * a wrong answer must say which rule produced it, so `architecture_evidence` records
    the matched product strings, not just the label.
"""
E_CLASS = "E_jumbo_or_unresolved"
_BASE = "stage0.architecture_typing"


def _products(cds_rows):
    return [(r["cds_id"], (r.get("product") or "").lower()) for r in cds_rows]


def _find(products, term):
    """Every CDS whose product contains this term. Substring, lowercased."""
    t = term.lower()
    return [(cid, p) for cid, p in products if t in p]


def _satisfied(products, spec):
    """Is one `all_of` group fully present? Returns (bool, matched evidence)."""
    matched = []
    for term in spec.get("all_of", []):
        hits = _find(products, term)
        if not hits:
            return False, []
        matched.append({"term": term, "cds_id": hits[0][0], "product": hits[0][1]})
    return True, matched


def _any_group(products, groups):
    for spec in groups or []:
        ok, matched = _satisfied(products, spec)
        if ok:
            return True, matched
    return False, []


def _supporting(products, terms):
    out = []
    for t in terms or []:
        hits = _find(products, t)
        if hits:
            out.append({"term": t, "cds_id": hits[0][0], "product": hits[0][1]})
    return out


def type_genome(cds_rows, rules):
    """Return the architecture prior plus everything needed to argue with it."""
    sig = rules.get(_BASE + ".signatures")
    products = _products(cds_rows)

    # ---- B: contractile tail
    b_ok, b_ev = _any_group(products, sig["B_myo_baseplate"].get("strong_any_of"))
    b_sup = _supporting(products, sig["B_myo_baseplate"].get("supporting"))

    # ---- D: Schitoviridae, requires the lineage-specific virion RNAP.
    # A bare "rna polymerase" is explicitly not accepted -- listing it as a forbidden
    # marker keeps a future edit from quietly widening this into "any polymerase".
    d_ok, d_ev = _any_group(products, sig["D_N4_schitoviridae"].get("unique_any_of"))
    d_sup = _supporting(products, sig["D_N4_schitoviridae"].get("supporting"))

    # ---- C: T7-like short tail
    c_ok, c_ev = _any_group(products, sig["C_podo_T7"].get("strong_any_of"))
    c_sup = _supporting(products, sig["C_podo_T7"].get("supporting"))

    # ---- A: long non-contractile tail. The tape measure fires in contractile tails
    # too, so A is only reachable when B's signature did NOT fire.
    a_terms = sig["A_sipho_gpJ"].get("strong_all_of_any") or []
    a_hits = [h for t in a_terms for h in _find(products, t)]
    a_ok = bool(a_hits)
    a_ev = ([{"term": a_terms[0], "cds_id": a_hits[0][0], "product": a_hits[0][1]}]
            if a_hits else [])
    a_sup = _supporting(products, sig["A_sipho_gpJ"].get("supporting"))

    strong = [name for name, ok in (("B_myo_baseplate", b_ok),
                                    ("C_podo_T7", c_ok),
                                    ("D_N4_schitoviridae", d_ok)) if ok]
    conflicts = []

    def result(cls, conf, evidence, note):
        return {
            "architecture_prior": cls,
            "architecture_confidence": conf,
            "architecture_evidence": evidence,
            "architecture_conflicts": conflicts,
            "rule_fired": note,
        }

    # ---- resolution order, exactly as frozen in the YAML
    if len(strong) > 1:
        conflicts.append({
            "classes": strong,
            "detail": "more than one strong signature satisfied; refusing to choose",
            "evidence": {"B": b_ev, "C": c_ev, "D": d_ev},
        })
        return result(E_CLASS, "low", b_ev + c_ev + d_ev,
                      "conflicting_signatures -> E")

    if d_ok:
        return result("D_N4_schitoviridae", "high", d_ev + d_sup,
                      "D unique virion-RNAP signature satisfied")
    if b_ok:
        return result("B_myo_baseplate", "high", b_ev + b_sup,
                      "B contractile signature satisfied")
    if c_ok:
        return result("C_podo_T7", "high" if c_sup else "medium", c_ev + c_sup,
                      "C short-tail signature satisfied, no competing strong signature")
    if a_ok:
        # B's absence is used ONLY here, to resolve A against the class that shares
        # the tape-measure marker. It is never A's positive evidence.
        return result("A_sipho_gpJ", "high" if a_sup else "medium", a_ev + a_sup,
                      "A tape-measure signature satisfied and no contractile signature")

    return result(E_CLASS, "low", [], "no strong signature satisfied -> E")


def resolve(cds_rows, rules, user_choice=None):
    """Auto-type, then let an explicit --architecture win -- and record both.

    The override is recorded rather than silently applied so a benchmark can always
    tell whether a number came from the typer or from a human who knew the answer.
    """
    auto = type_genome(cds_rows, rules)
    used = user_choice or auto["architecture_prior"]
    return used, {
        "auto_prediction": auto["architecture_prior"],
        "auto_confidence": auto["architecture_confidence"],
        "architecture_evidence": auto["architecture_evidence"],
        "architecture_conflicts": auto["architecture_conflicts"],
        "rule_fired": auto["rule_fired"],
        "user_override": user_choice,
        "override_used": bool(user_choice) and user_choice != auto["architecture_prior"],
    }
