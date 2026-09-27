"""RUN_REPORT.md -- the human-readable half of a run."""


def _tier(c):
    return "%s-%s" % (c["tier_r"], c["tier_e"])


def write(run, ranked, screening, path, meta):
    L = []
    A = L.append
    by_id = {c["cds_id"]: c for c in ranked}
    A("# RBPfinder run report -- %s" % run["phage_id"])
    A("")
    A("Generated with zero human judgement: every threshold, keyword, mapping and tier")
    A("rule was read from `rules/decision_rules.yaml`.")
    A("")

    # ---- the product contract, in the order the contract states it. The internal
    # pool is an implementation detail and no longer opens the report: judging the
    # tool by a 132-protein pool when the ask is 4 proteins is a category error.
    def _row(cid):
        c = by_id.get(cid)
        if not c:
            return "| %s | | | |" % cid
        return "| `%s` | %s | %s | %s |" % (
            cid, _tier(c), c["protein_role"],
            c["annotation"]["product"] or "(no product)")

    A("## Primary candidates")
    A("")
    A("Strongest evidence for a direct receptor-binding role. **Start here.**")
    A("")
    if run.get("primary_set"):
        A("| CDS | tier | role | annotation |")
        A("|---|---|---|---|")
        for cid in run["primary_set"]:
            A(_row(cid))
    else:
        A("_None. No candidate reached the primary threshold; see rescue candidates._")
    A("")
    A("## Rescue candidates")
    A("")
    A("Retained because excluding them would create an unacceptable false-negative risk.")
    A("")
    if run.get("rescue_set"):
        A("| CDS | tier | role | annotation |")
        A("|---|---|---|---|")
        for cid in run["rescue_set"]:
            A(_row(cid))
    else:
        A("_None._")
    A("")
    A("## Completeness")
    A("")
    A("| | |")
    A("|---|---|")
    A("| run status | `%s` |" % run.get("run_status", "completed"))
    A("| completeness assessment | **`%s`** -- sample-specific unresolved RBP risk |"
      % run.get("completeness_assessment", "unknown"))
    A("| assessment coverage | `%s` -- whether the optional audits could run here |"
      % run.get("assessment_coverage", "unknown"))
    f = run.get("completeness_facts") or {}
    if f.get("capped_by_coverage"):
        A("| sample-specific assessment | `%s` -- **capped to `%s` because this "
          "installation could not run every audit** |"
          % (f.get("sample_specific_assessment"),
             run.get("completeness_assessment")))
    elif f.get("sample_specific_assessment"):
        A("| sample-specific assessment | `%s` (uncapped) |"
          % f.get("sample_specific_assessment"))
    if f:
        A("| sequence-layer unresolved candidates | %s (searched, no hit) |"
          % f.get("sequence_layer_unresolved_candidates"))
        A("| candidates with unresolved RBP risk | %s |"
          % f.get("unresolved_rbp_risk_candidates"))
        A("| longest unresolved-risk run | %s CDS |"
          % f.get("longest_unresolved_tail_run_cds"))
        A("| unresolved loci | %s |" % f.get("unresolved_loci"))
    A("")
    if run.get("limitations"):
        A("Limitations bounding this result:")
        A("")
        for lim in run["limitations"]:
            tag = {"capability_unavailable": "installation",
                   "sample_specific": "this phage",
                   "informational": "note"}.get(lim.get("kind"), "")
            A("- [%s] %s" % (tag, lim["text"]))
        A("")
    else:
        A("No limitations recorded: the available evidence was sufficient to compress")
        A("the candidate space.")
        A("")
    tu = run.get("top_unresolved_candidates")
    if tu and tu["candidates"]:
        collapsed = tu["display"] == "display_collapsed_by_default"
        if collapsed:
            A("<details><summary>Top unresolved candidates for exploratory "
              "validation</summary>")
            A("")
        else:
            A("### Top unresolved candidates for exploratory validation")
            A("")
            A("No candidate has direct receptor-binding evidence strong enough for the")
            A("primary or rescue shortlist. These are shown so that there is still an")
            A("executable next step:")
            A("")
        A("| # | CDS | tier | families | annotation |")
        A("|---:|---|---|---|---|")
        for c_ in tu["candidates"]:
            A("| %d | `%s` | %s | %s | %s |"
              % (c_["rank"], c_["cds_id"], c_["tier"],
                 "+".join(c_["families"]) or "-", c_["product"] or "(no product)"))
        A("")
        A("_These are **not** promoted to rescue. They are shown because the evidence is")
        A("insufficient to produce a confident shortlist, and they are excluded from every")
        A("recall metric._")
        A("")
        if collapsed:
            A("</details>")
            A("")
    A("> The sections below are the internal record: the recall-protection pool, the")
    A("> evidence trace and the Stage 6 audit. They are how the shortlist above was")
    A("> reached, not additional proteins to test.")
    A("")
    A("## Run parameters")
    A("")
    A("| | |")
    A("|---|---|")
    A("| input mode | `%s` |" % run["input_mode"])
    A("| gene caller | `%s` |" % run["gene_caller"])
    A("| architecture | `%s` (soft prior only) |" % run["architecture"])
    A("| k_default | %s -- display parameter, never a cap |" % run["k_default"])
    A("| CDS in genome | %d |" % len(screening))
    A("| internal candidate pool | %d (recall protection, not the shortlist) |" % len(ranked))
    A("| C1 window | %s |" % meta["c1"].get("detail"))
    A("| C4 gap threshold | %s bp |" % meta["c4"]["gap_threshold_bp"])
    A("")
    pol = meta.get("policy")
    if pol is not None:
        A("## Data handling")
        A("")
        for line in pol.summary_lines():
            A("- %s" % line)
        A("")
    A("## Internal pool, ranked")
    A("")
    A("| # | CDS | tier | role | layer | families | channels | annotation |")
    A("|---:|---|---|---|---|---|---|---|")
    for i, c in enumerate(ranked, 1):
        A("| %d | %s | %s | %s | %s | %s | %s | %s |" % (
            i, c["cds_id"], _tier(c), c["protein_role"], c["definition_layer"],
            "+".join(c.get("independent_families", [])) or "-",
            ",".join(c["nomination_channels"]), c["annotation"]["product"]))
    A("")
    # ---- Evidence capabilities, and the ceiling, both read from the RUN ----
    # This paragraph used to be a constant. It asserted "No Stage 2 (sequence) ...
    # evidence was collected" in every report, including reports whose own table above
    # showed D+G+S. Now both sections are derived from the EvidenceRecords the run
    # produced, so the report cannot contradict itself.
    cap_rows = meta.get("capability_rows") or []
    if cap_rows:
        A("## Evidence capabilities")
        A("")
        A("| capability | this run | can import a result | executor |")
        A("|---|---|---|---|")
        for row in cap_rows:
            A("| %s -- %s | **%s** | %s | %s |"
              % (row["group"], row["label"], row["run_state"],
                 "yes" if row["result_import"] == "available" else "no",
                 row["executor_label"]))
        A("")
        A("A batch file (`HHPRED_BATCH.tsv`, `AF3_MONOMER_BATCH.tsv`) is a **request "
          "list**: work still to be done. Its existence is not evidence that the work "
          "happened.")
        A("")
    A("## Ceiling of this run")
    A("")
    for line in (meta.get("ceiling_lines") or
                 ["The evidence available in this run limits the reachable tier."]):
        A(line)
    A("")
    if meta.get("phold_phrog_derived"):
        A("Phold's top hits are internal PHROG-derived database entries (`protein<N>`), not")
        A("PDB/AFDB/BFVD structures, so they contribute **D, never an independent T**.")
        A("")
    ca = run["completeness_audit"]
    A("## Completeness audit (Stage 6)")
    A("")
    A("| | |")
    A("|---|---|")
    A("| reopen cycles | %d |" % ca["reopen_cycles"])
    A("| cap reached | %s |" % ("yes" if ca["cap_reached"] else "no"))
    A("| terminated cleanly | **%s** |" % str(ca["terminated_cleanly"]).lower())
    A("")
    if not ca["terminated_cleanly"]:
        A("`terminated_cleanly = false` is the correct result here: at least one audit could")
        A("not run. An unexecuted audit is never reported as a passed audit.")
        A("")
    for a in ca["audits"]:
        state = "resolved" if a["resolved"] else "**unresolved**"
        A("### `%s` -- %s" % (a["audit_id"], state))
        A("")
        A("> %s" % a["question"])
        A("")
        if a["hits"]:
            for h in a["hits"]:
                A("- `%s` -> **%s**" % (h["cds_id"], h["outcome"]))
                A("  - %s" % h["written_reason"])
        else:
            A("- no hits")
        A("")
    if ca["unresolved_cds"]:
        A("**Unresolved:**")
        A("")
        for u in ca["unresolved_cds"]:
            A("- `%s` -- %s" % (u["cds_id"], u["written_reason"]))
        A("")
    if run.get("heuristic_disclaimers"):
        A("## Uncalibrated indicators used")
        A("")
        for d in run["heuristic_disclaimers"]:
            A("- %s" % d)
        A("")
    if meta.get("ingest_notes"):
        A("## Ingest reconciliation notes")
        A("")
        for n in meta["ingest_notes"]:
            A("- %s" % n)
        A("")
    A("## Audit trail")
    A("")
    A("`all_CDS_screening.tsv` carries one row per CDS with a non-empty `reason`,")
    A("including every CDS that did NOT become a candidate.")
    open(path, "w", encoding="utf-8", newline="\n").write("\n".join(L) + "\n")
