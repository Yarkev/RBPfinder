"""Stage 6 — the RBP completeness challenge.

This is an adversarial recall-recovery mechanism, not a checklist. Each audit
asks "if my current result is incomplete, which CDS did I most likely miss?",
and a hit REOPENS the candidate pool rather than printing a warning.

Two rules govern honesty here:
  * an audit that cannot run reports itself unexecutable; it never reports "pass";
  * a phold hit against phold's own PHROG-derived database is D-family evidence
    and may never be promoted to T just to make A2 runnable.
"""
import re

_QUESTIONS = {
    "A1": "stage6.audits.A1_module_sweep.question",
    "A2": "stage6.audits.A2_context_negative_signal_positive.question",
    "A3": "stage6.audits.A3_neighbourhood.question",
    "A4": "stage6.audits.A4_variable_locus_accounting.question",
}


def _module_of(rec, rules):
    mm = rules.get("module_map")
    return mm.get(rec["function"], mm.get("_default"))


def genome_wide_T_or_M_available(cds_rows, rules, ml_results=None):
    """Capability probe for A2. Returns (available, detail).

    The test is a WHITELIST: a hit counts as a structural source only when its
    target is a real structure entry (PDB / AFDB / BFVD). Blacklisting phold's
    internal namespaces instead was whack-a-mole -- `protein<N>` was covered,
    then `envhog_*` appeared on T7, then `WP_*` (an NCBI RefSeq protein) on
    lambda. Every one of those is sequence-derived, so reading any of them as T
    would double-count the same datum that already sits in D.
    """
    pattern = rules.get("evidence_families.structure_database_tophit_pattern")
    qualifying = [
        r["cds_id"] for r in cds_rows
        if r.get("tophit_protein") and re.match(pattern, r["tophit_protein"])
    ]
    if qualifying:
        return True, ("%d CDS carry a hit to a real structure database" % len(qualifying))
    if ml_results:
        return True, "machine-learning results present"
    return False, (
        "no phold tophit matches a structure-database identifier (%s); every hit is to "
        "one of phold's sequence-derived bundled databases, so it is D-family evidence "
        "and no genome-wide T or M source exists in this run" % pattern
    )


def a1_module_sweep(cds_rows, pool, rules):
    rc = rules.get("stage6.reason_classes.A1")
    hits = []
    for rec in cds_rows:
        if rec["cds_id"] in pool:
            continue
        if _module_of(rec, rules) == "tail_morphogenesis":
            hits.append({
                "audit_id": "A1",
                "cds_id": rec["cds_id"],
                "reason_class": rc,
                "why_flagged": "annotated in the tail/morphogenesis module (%s) but never "
                               "entered pool P" % (rec["product"] or "no product"),
            })
    return hits


def a1b_tail_adjacent_unknown(cds_rows, pool, rules):
    """A1b -- deliberately blind to the C1 window.

    A dark-matter RBP is 'unknown function' by definition, so A1a, which only
    sees the tail_morphogenesis module, cannot flag it. A1b walks outward from
    each real tail component instead, which still works when C1 built every one
    of its windows in the wrong place.
    """
    base = "stage6.audits.A1_module_sweep.sub_checks.A1b"
    radius = rules.get(base + ".adjacent_unknown_radius_cds")
    max_chain = rules.get(base + ".max_unknown_chain")
    rc = rules.get("stage6.reason_classes.A1b")

    n = len(cds_rows)
    is_tail = [_module_of(r, rules) == "tail_morphogenesis" for r in cds_rows]

    def unknown(r):
        return (r.get("phrog") is None
                or r.get("function") == "unknown function"
                or "hypothetical" in (r.get("product") or "").lower())

    hits, seen = [], set()
    for i, tail in enumerate(is_tail):
        if not tail:
            continue
        for direction in (-1, 1):
            chain = 0
            for step in range(1, radius + 1):
                j = i + direction * step
                if j < 0 or j >= n:
                    break
                rec = cds_rows[j]
                if is_tail[j]:
                    chain = 0
                    continue
                if not unknown(rec):
                    break
                chain += 1
                if chain > max_chain:
                    break
                if rec["cds_id"] in pool or rec["cds_id"] in seen:
                    continue
                seen.add(rec["cds_id"])
                hits.append({
                    "audit_id": "A1",
                    "cds_id": rec["cds_id"],
                    "reason_class": rc,
                    "why_flagged": "unannotated CDS %d position(s) from tail component %s, "
                                   "never entered pool P" % (step, cds_rows[i]["cds_id"]),
                })
    return sorted(hits, key=lambda h: h["cds_id"])


def a3_neighbourhood(cds_rows, pool, candidates, rules, is_circular):
    n = rules.get("stage6.audits.A3_neighbourhood.n_cds")
    tiers = rules.get("stage6.a3_applies_to.tiers")
    layers = rules.get("stage6.a3_applies_to.also_any_definition_layer")
    rc = rules.get("stage6.reason_classes.A3")

    idx = {r["cds_id"]: i for i, r in enumerate(cds_rows)}
    total = len(cds_rows)
    anchors = sorted(
        c["cds_id"] for c in candidates
        if c["tier_r"] in tiers or c["definition_layer"] in layers
    )

    hits, covered = [], []
    for cid in anchors:
        i = idx.get(cid)
        if i is None:
            continue
        for off in range(-n, n + 1):
            if off == 0:
                continue
            j = i + off
            if is_circular:
                j %= total
            elif j < 0 or j >= total:
                continue
            nb = cds_rows[j]["cds_id"]
            if nb in pool:
                covered.append(nb)
                continue
            hits.append({
                "audit_id": "A3",
                "cds_id": nb,
                "reason_class": rc,
                "why_flagged": "within +/-%d CDS of assigned candidate %s but not in pool P"
                               % (n, cid),
            })
    uniq, seen = [], set()
    for h in sorted(hits, key=lambda x: (x["cds_id"], x["why_flagged"])):
        if h["cds_id"] in seen:
            continue
        seen.add(h["cds_id"])
        uniq.append(h)
    return uniq, sorted(set(covered)), anchors


def a4_variable_locus_accounting(pool, c5_result, rules):
    """Is every comparative variable locus explained by a candidate already in P?

    The per-CDS C5 test nominates the variable CDS themselves. A locus is wider than
    that: it runs from conserved flank to conserved flank, and a swapped tip travels
    with its adapter. A member that the per-CDS test did not nominate -- because it is
    annotated as something else, or is too short to look interesting -- is exactly the
    kind of CDS this project exists to stop losing, so the LOCUS is what gets audited.
    """
    rc = rules.get("stage6.reason_classes.A4")
    hits = []
    for locus in c5_result.get("loci") or []:
        unexplained = [cid for cid in locus["members"] if cid not in pool]
        for cid in unexplained:
            hits.append({
                "audit_id": "A4",
                "cds_id": cid,
                "reason_class": rc,
                "why_flagged":
                    "member of comparative variable locus %s (flanks %s/%s conserved in "
                    "%s/%s of the cohort) but never entered pool P"
                    % (locus["locus_id"], locus["left_conserved"],
                       locus["right_conserved"], locus["left_presence"],
                       locus["right_presence"]),
            })
    return hits


def _a4_state(c5_result, rules):
    """Three states. `not implemented` and `nothing to compare against` are different.

    A single-genome run has ANSWERED the variable-locus question -- there are no
    comparators, so there are no variable loci to leave unexplained. Reporting that as
    an unresolved audit would make terminated_cleanly=false the normal case and teach
    everyone to ignore it.
    """
    base = "stage6.a4_capability"
    if c5_result is None:
        return {"executable": False, "blocker": rules.get(base + ".on_unavailable"),
                "detail": "channel C5 did not run in this execution"}
    state = c5_result.get("state")
    if state != "executable":
        cohort = c5_result.get("cohort") or {}
        return {
            "executable": False,
            "not_applicable": bool(rules.get(base + ".no_cohort_counts_as_resolved")),
            "blocker": rules.get(base + ".on_no_cohort"),
            "detail": "C5 is implemented but no comparator cohort was available (%s)"
                      % (cohort.get("why") or "no cohort supplied"),
        }
    return {"executable": True,
            "detail": "%d comparative variable loci accounted for against a cohort of %d"
                      % (len(c5_result.get("loci") or []),
                         len((c5_result.get("cohort") or {}).get("chosen") or []))}


def run_cycle(cds_rows, pool, candidates, rules, is_circular, ml_results=None,
              c5_result=None):
    """One adversarial pass. Returns (hits, executability)."""
    hits = list(a1_module_sweep(cds_rows, pool, rules))
    a1b_hits = a1b_tail_adjacent_unknown(cds_rows, pool, rules)
    hits.extend(a1b_hits)
    a3_hits, a3_covered, a3_anchors = a3_neighbourhood(
        cds_rows, pool, candidates, rules, is_circular
    )
    hits.extend(a3_hits)

    a2_ok, a2_detail = genome_wide_T_or_M_available(cds_rows, rules, ml_results)
    exec_state = {
        "A1": {"executable": True,
               "detail": "A1a swept %d CDS; A1b flagged %d unannotated CDS adjacent to a "
                         "tail component" % (len(cds_rows), len(a1b_hits))},
        "A2": {
            "executable": a2_ok,
            "blocker": None if a2_ok else rules.get("stage6.a2_capability.on_unavailable"),
            "detail": a2_detail,
        },
        "A3": {
            "executable": True,
            "detail": "anchors=%d, neighbours already covered=%d"
                      % (len(a3_anchors), len(a3_covered)),
        },
        "A4": _a4_state(c5_result, rules),
    }
    if exec_state["A4"]["executable"]:
        hits.extend(a4_variable_locus_accounting(pool, c5_result, rules))
    if a2_ok:
        raise NotImplementedError(
            "A2 became executable (a genome-wide T/M source appeared) but the scan is not "
            "implemented; refusing to report A2 as passed"
        )
    return hits, exec_state


def build_result(all_hits, exec_state, outcomes, cycles, cap_reached, rules):
    by_audit = {a: [] for a in ("A1", "A2", "A3", "A4")}
    for h in all_hits:
        by_audit[h["audit_id"]].append({
            "cds_id": h["cds_id"],
            "why_flagged": h["why_flagged"],
            "outcome": outcomes.get((h["audit_id"], h["cds_id"]), "documented_unresolved"),
            "written_reason": h.get("written_reason", h["why_flagged"]),
        })

    audits, unresolved = [], []
    for aid in ("A1", "A2", "A3", "A4"):
        st = exec_state[aid]
        hits = sorted(by_audit[aid], key=lambda x: x["cds_id"])
        # `not_applicable` is a RESOLVED state: the capability exists and the question
        # has an answer ("there was nothing to compare against"). A missing capability
        # is not -- it has answered nothing. See stage6.a4_capability.states.
        not_applicable = bool(st.get("not_applicable"))
        resolved = (bool(st["executable"]) or not_applicable) and all(
            h["outcome"] != "documented_unresolved" for h in hits
        )
        audits.append({
            "audit_id": aid,
            "question": rules.get(_QUESTIONS[aid]),
            "hits": hits,
            "resolved": resolved,
            "executability": ("executable" if st["executable"]
                             else st.get("blocker") or "not_executable"),
            "executability_detail": st.get("detail"),
        })
        if not st["executable"] and not not_applicable:
            unresolved.append({
                "cds_id": "*",
                "written_reason": "%s: %s — %s" % (aid, st["blocker"], st["detail"]),
            })
        for h in hits:
            if h["outcome"] == "documented_unresolved":
                unresolved.append({"cds_id": h["cds_id"], "written_reason": h["written_reason"]})

    return {
        "audits": audits,
        "reopen_cycles": cycles,
        "cap_reached": cap_reached,
        "unresolved_cds": unresolved,
        "terminated_cleanly": all(a["resolved"] for a in audits),
    }
