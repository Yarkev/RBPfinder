"""Turn internal audit state into the product-level completeness statement.

Three fields, because three different questions were being answered by one flag:

    run_status               did the program finish            completed | failed
    assessment_coverage      were the optional audits even     full | reduced
                             available in this installation
    completeness_assessment  how much SAMPLE-SPECIFIC          complete | partial | limited
                             unresolved RBP risk remains

M14A exists because the first version collapsed these. Every one of 48 genomes came
back `limited`, driven by one environment gap (no genome-wide T/M source on this
machine, so audit A2 cannot run) and one near-universal fact (somewhere in the tail
module a protein has no homolog). A signal that never varies cannot inform anyone.

Two calibration rules carry the fix:

  * an environment-wide capability gap sets `coverage = reduced` and caps the
    assessment at `partial`. It never produces `limited` on its own -- a well-resolved
    phage on an under-equipped machine is not the same thing as an intractable one.
  * "no homolog for some tail protein" is a FACT, not a severity. It becomes `limited`
    only when the proteins without evidence form a contiguous stretch that cannot be
    ruled out, or when an audit/C5 locus is left unexplained.

And the C5 三态 model is respected: `no comparator available` is the RESOLVED state of a
capability that exists, so it is informational and does not lower anything.
"""
_BASE = "product_contract"


def _source(rules, lid):
    for item in rules.get(_BASE + ".limitation_sources"):
        if item["id"] == lid:
            return item
    raise KeyError(lid)


def _mk(rules, lid, severity=None, **kw):
    src = _source(rules, lid)
    return {"id": lid, "kind": src["kind"],
            "severity": severity or src.get("severity"),
            "text": src["text"].strip().format(**kw)}


def _scale_severity(longest, rules):
    """partial while the dark run is localized; limited once it reaches module scale.

    Detection and severity are separate questions. `>= contiguous_run_min` says an
    unresolved locus EXISTS -- true on every SMA genome, which is a fact about the
    dataset, not a broken rule. Severity asks whether the unresolved span is large
    enough to hide a whole apparatus-scale module, and that anchor (max_cluster_cds)
    already existed for a different purpose.
    """
    scale = rules.get(_BASE + ".unresolved_locus_rule.module_scale_cds")
    return "limited" if longest >= scale else "partial"


def unresolved_rbp_risk(recs, rules):
    """Is this CDS still able to be carrying an unresolved receptor-binding role?

    The question is NOT "does it have an annotation". M14B's P22 control showed why:
    `head scaffolding protein` and `hypothetical protein` both produce a
    directionally-uninformative record, yet the first is a settled identity and the
    second is not. Deciding on annotation presence would have swapped one category error
    for another.

    Resolution comes from evidence DIRECTION:

        supports_non_rbp_identity / supports_structural_only  -> settled, not at risk
        supports_receptor_binding                             -> already on the shortlist
        supports_apparatus_membership                         -> NOT settled

    That last line is load-bearing. A specific tail name says the protein belongs to the
    apparatus, never that it does not also touch the receptor -- N4 gp65 is annotated
    "tail sheath and receptor binding protein" and is a real dual-role RBP.
    """
    base = _BASE + ".rbp_resolution_state"
    resolving = set(rules.get(base + ".resolved_if_any"))
    resolving |= set(rules.get(base + ".also_resolved_if"))
    directions = {r["direction"] for r in recs if not r.get("superseded_by")}
    if directions & resolving:
        return False
    # tail / adsorption context is required: this measure is about the apparatus, not
    # about every unannotated CDS in the genome.
    return any(r["family"] == "G" and r["direction"] != "uninformative" for r in recs)


def _tail_runs(exhausted, cds_order, rules):
    """Contiguous stretches of evidence-free tail CDS, in genome order.

    One protein with no homolog is ordinary. A run of adjacent tail CDS where nothing --
    sequence, profile or structure -- has anything to say is a module nobody resolved,
    and an RBP cannot be excluded from it.
    """
    base = _BASE + ".unresolved_locus_rule"
    need = rules.get(base + ".contiguous_run_min")
    gap = rules.get(base + ".adjacency_gap_cds")
    idx = {cid: i for i, cid in enumerate(cds_order)}
    pos = sorted(idx[c] for c in exhausted if c in idx)
    runs, cur = [], []
    for p in pos:
        if cur and p - cur[-1] <= gap + 1:
            cur.append(p)
        else:
            if len(cur) >= need:
                runs.append(cur)
            cur = [p]
    if len(cur) >= need:
        runs.append(cur)
    return [[cds_order[i] for i in r] for r in runs]


def build(run, candidates, records_by_id, rules, c5_state=None, c5_loci=None,
          shortlist=(), cds_order=(), extra_limitations=()):
    """Return (run_status, completeness_assessment, assessment_coverage, limitations)."""
    audit = run["completeness_audit"]
    limitations = []

    # A backend that could not finish is a coverage gap, not a sample property: the
    # same shape as a missing local capability, so it caps the assessment without ever
    # producing `limited` on its own.
    for lim in extra_limitations or ():
        limitations.append({"id": lim["id"], "kind": "capability_unavailable",
                            "severity": None, "text": lim["text"]})

    for a in audit["audits"]:
        if not a["resolved"]:
            limitations.append(_mk(rules, "audit_not_executable", audit=a["audit_id"],
                                   blocker=a.get("executability") or "unknown"))

    if c5_state and c5_state != "executable":
        limitations.append(_mk(rules, "no_comparative_cohort"))

    # Two DIFFERENT measures, deliberately kept apart.
    #
    #   exhausted     the sequence layer ran and found nothing -- a fact about the search,
    #                 countable only because searched-no-hit and never-run are separate.
    #   at_risk       the RBP-resolution state is still open -- a fact about knowledge.
    #
    # M14A collapsed them and P22 paid for it: proteins whose identity was settled were
    # counted as unresolved because their annotation said nothing about receptor binding.
    exhausted, at_risk = [], []
    for c in candidates:
        recs = records_by_id.get(c["cds_id"], [])
        s = next((r for r in recs if r["family"] == "S"), None)
        if (s and not s.get("not_run") and s["direction"] == "uninformative"
                and any(r["family"] == "G" and r["direction"] != "uninformative"
                        for r in recs)):
            exhausted.append(c["cds_id"])
        if unresolved_rbp_risk(recs, rules):
            at_risk.append(c["cds_id"])

    if exhausted:
        limitations.append(_mk(rules, "sequence_evidence_exhausted_tail",
                               n=len(exhausted)))
        limitations[-1]["cds_ids"] = sorted(exhausted)

    if at_risk:
        runs = _tail_runs(at_risk, list(cds_order), rules)
        if runs:
            longest = max(len(r) for r in runs)
            lim = _mk(rules, "unresolved_rbp_risk_locus",
                      severity=_scale_severity(longest, rules),
                      n=len(runs), longest=longest)
            lim["loci"] = runs
            lim["longest_run_cds"] = longest
            limitations.append(lim)

    shortlist = set(shortlist or ())
    unresolved_loci = [L for L in (c5_loci or [])
                       if not (set(L.get("members") or []) & shortlist)]
    if unresolved_loci:
        # Scale, not mere presence: "there is a C5 locus" is not a shortcut to `limited`.
        longest_c5 = max(len(L.get("members") or []) for L in unresolved_loci)
        lim = _mk(rules, "unresolved_variable_tail_locus",
                  severity=_scale_severity(longest_c5, rules), n=len(unresolved_loci))
        lim["loci"] = [L["locus_id"] for L in unresolved_loci]
        lim["longest_locus_cds"] = longest_c5
        limitations.append(lim)

    if audit.get("cap_reached"):
        limitations.append(_mk(rules, "reopen_cap_reached"))

    unresolved = [u for u in audit.get("unresolved_cds", []) if u["cds_id"] != "*"]
    if unresolved:
        lim = _mk(rules, "unresolved_cds", n=len(unresolved))
        lim["cds_ids"] = sorted(u["cds_id"] for u in unresolved)
        limitations.append(lim)

    # ---- derivation, exactly as frozen in product_contract.completeness_assessment
    coverage = ("reduced" if any(l["kind"] == "capability_unavailable" for l in limitations)
                else "full")
    sample = [l for l in limitations if l["kind"] == "sample_specific"]

    # The uncapped judgement first: what the SAMPLE evidence alone says. Losing this is
    # how "this phage looks converged but the install is incomplete" and "this phage has
    # an unresolved module" end up printing the same word.
    if any(l["severity"] == "limited" for l in sample):
        sample_specific = "limited"
    elif sample:
        sample_specific = "partial"
    else:
        sample_specific = "complete"

    # then the cap: a reduced install may lower a complete to partial, never to limited.
    assessment = sample_specific
    if coverage == "reduced" and sample_specific == "complete":
        assessment = "partial"
    # The facts layer stays visible in its own right. A colour alone tells an
    # experimentalist far less than "18 unresolved candidates, longest dark run 7 CDS".
    facts = {
        "sample_specific_assessment": sample_specific,
        "capped_by_coverage": assessment != sample_specific,
        "sequence_layer_unresolved_candidates": len(exhausted),
        "unresolved_rbp_risk_candidates": len(at_risk),
        "longest_unresolved_tail_run_cds": max(
            (l.get("longest_run_cds", 0) for l in limitations), default=0),
        "unresolved_loci": sum(len(l.get("loci") or [])
                               for l in limitations
                               if l["id"] == "unresolved_rbp_risk_locus"),
    }
    return "completed", assessment, coverage, limitations, facts
