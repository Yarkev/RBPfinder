"""M19-b1/b2 -- one canonical answer to "what did this run actually collect".

Three axes, because three different questions get asked and conflating any two of them
produces the misreading the field report caught:

    result_import   can RBPfinder parse a result of this kind if handed one?
    executor        does this installation run the tool itself?
    run_state       did it happen in THIS run?

HHpred is simultaneously `available` / `not_bundled` / `not_run`. Collapsed into one
boolean it becomes either "HHpred works" or "HHpred is unsupported", and both are wrong.
`HHPRED_BATCH.tsv` exists on disk either way, which is exactly why a user reads it as a
result.

`run_state` is derived from the EvidenceRecords the run produced -- never from which
code path executed. The RUN_REPORT Ceiling section used to be hardcoded prose asserting
"No Stage 2 (sequence) ... evidence was collected" while the same report's table showed
`D+G+S`. A report that contradicts itself is worse than one that says less.
"""
_BASE = "capability_matrix"

COMPLETED = "COMPLETED"
PARTIAL = "PARTIAL"
NOT_RUN = "NOT RUN"
NOT_APPLICABLE = "NOT APPLICABLE"
FAILED = "FAILED"
# M21-a. A user who declined is not a user nobody asked: NOT RUN already means "nobody
# looked", and merging the two costs the report its explanation for the coverage.
DECLINED = "DECLINED"
# M21-b. A batch file on disk is a REQUEST, not a result -- HHPRED_BATCH.tsv was once
# read as though HHpred had run. And "we asked and are waiting" is a third fact, distinct
# from "nobody looked" and from "the user said no", by exactly the argument that made
# DECLINED its own word.
AWAITING_USER = "AWAITING USER"
REQUEST_GENERATED = "REQUEST GENERATED"

_EXECUTOR_LABEL = {
    "bundled": "executor bundled",
    "not_bundled": "executor NOT BUNDLED",
    "configured": "executor requires configuration",
    "not_configured": "executor NOT CONFIGURED",
    "request_list_only": "REQUEST LIST ONLY",
}


def families_collected(candidates):
    """Which evidence families this run actually produced, per the records themselves.

    Reads the records, not the provenance. Manifest-imported sequence evidence IS
    sequence evidence: whichever path delivered it, an S record exists and the report
    must not claim otherwise.
    """
    fams = {}
    for cand in candidates or []:
        for region in cand.get("regions") or []:
            for rec in region.get("evidence") or []:
                fam = rec.get("family")
                if not fam:
                    continue
                bucket = fams.setdefault(fam, {"total": 0, "searched": 0,
                                                "tools": set(), "searched_tools": set()})
                bucket["total"] += 1
                if rec.get("tool"):
                    bucket["tools"].add(rec["tool"])
                if not rec.get("not_run"):
                    bucket["searched"] += 1
                    if rec.get("tool"):
                        # Only a record that was actually produced counts. A placeholder
                        # standing for "no domain evidence" is not domain evidence.
                        bucket["searched_tools"].add(rec["tool"])
    return fams


def _run_state(cap, fams, ctx):
    """Derive this run's state for one capability, from evidence and explicit context."""
    fam = cap.get("family")
    # Which tools actually produced evidence for this capability. Keying on the family
    # alone was wrong and visibly so: pharokka's PHROG call is family D, so InterProScan
    # and HHpred both reported COMPLETED on a run where neither had ever run.
    owned = set(cap.get("tools") or [])
    searched_tools = {t for b in fams.values() for t in b["searched_tools"]}
    evidenced = bool(owned & searched_tools)
    # Checked before anything else. A provider the user declined has its state decided by
    # that decision, not by what evidence happens to exist -- and the answer must not be
    # NOT RUN, which already means "nobody looked".
    acq = (ctx.get("acquisition_modes") or {}).get(cap["id"], {})
    if acq.get("mode") == "SKIP":
        return DECLINED
    # A written batch moves the row off NOT RUN and nowhere near COMPLETED. Only real
    # evidence records can do that, further down.
    pending = {"awaiting_user_results": AWAITING_USER,
               "request_generated": REQUEST_GENERATED,
               "blocked_on_input": AWAITING_USER}.get(acq.get("status"))
    if pending and not (owned & searched_tools):
        return pending
    if cap["id"] == "genome_annotation":
        return COMPLETED if ctx.get("annotated_input") else NOT_RUN
    if cap["id"] == "c5_comparative":
        state = ctx.get("c5_state")
        if state == "ok":
            return COMPLETED
        return NOT_APPLICABLE if state == "no_cohort" else NOT_RUN
    if cap["id"] == "uniprot_enrichment":
        # Five states, not three. The earlier version returned NOT RUN whenever nothing
        # came back, which folded two quite different runs into the same word: one where
        # there was nothing to look up, and one where every lookup was attempted and
        # failed. The second is the report saying "we never looked" about a run that
        # looked five times and got nothing -- the same empty-vs-absent collapse the
        # Stage 2 records were fixed for. None of these changes the evidence family.
        enr = ctx.get("uniprot_enrichment") or {}
        if not enr.get("requested"):
            return NOT_RUN                       # never looked
        asked = enr.get("entries_requested") or 0
        got = enr.get("entries_retrieved") or 0
        if not asked:
            return NOT_APPLICABLE                # looked; there was nothing to look up
        if enr.get("completed"):
            return COMPLETED                     # looked; got all of it
        return PARTIAL if got else FAILED        # got some / attempted and got none
    if cap["id"] == "ebi_blastp":
        remote = ctx.get("remote") or {}
        if not remote:
            return NOT_RUN
        if remote.get("systemic_transport_failure"):
            return FAILED
        if remote.get("failed"):
            return PARTIAL
        return COMPLETED if remote.get("successful") else NOT_RUN
    # A capability whose executor is not bundled can still be COMPLETED -- someone ran
    # it elsewhere and the result was imported. That is the whole point of keeping the
    # executor and run_state axes apart.
    return COMPLETED if evidenced else NOT_RUN


def build(rules, candidates=None, context=None):
    """Return the matrix rows. `context` carries what evidence records cannot say."""
    ctx = context or {}
    fams = families_collected(candidates)
    rows = []
    for cap in rules.get(_BASE + ".capabilities"):
        rows.append({
            "id": cap["id"],
            "group": cap["group"],
            "label": cap["label"],
            "family": cap.get("family"),
            "tools": list(cap.get("tools") or []),
            "result_import": cap["result_import"],
            "executor": cap["executor"],
            "executor_label": _EXECUTOR_LABEL.get(cap["executor"], cap["executor"]),
            "run_state": _run_state(cap, fams, ctx),
            "note": cap.get("note", ""),
        })
    return rows


def render(rows, detail=None):
    """Plain text, grouped. Used by both `doctor` and the report."""
    out, group = [], None
    for row in rows:
        if row["group"] != group:
            out.append("")
            out.append(row["group"])
            group = row["group"]
        extra = (detail or {}).get(row["id"], "")
        out.append("  %-24s %-16s %s" % (row["label"], row["run_state"],
                                         extra or row["executor_label"]))
        if row["executor"] in ("not_bundled", "request_list_only") and extra:
            out.append("  %-24s %-16s %s" % ("", "", row["executor_label"]))
    return "\n".join(out).strip("\n")


def ceiling_paragraph(rows, fams):
    """What limited this run, written from what it actually collected.

    Replaces a hardcoded paragraph that asserted no Stage 2 evidence existed no matter
    what the run did.
    """
    present = sorted(f for f, b in fams.items() if b["searched"])
    missing = sorted({r["family"] for r in rows
                      if r["family"] and r["run_state"] in (NOT_RUN, NOT_APPLICABLE)}
                     - set(present))
    lines = []
    if present:
        lines.append("Evidence families collected in this run: **%s**."
                     % ", ".join(present))
    else:
        lines.append("**No evidence families were collected in this run.**")
    if missing:
        lines.append("Not collected: **%s**. Those are absent, which is not the same as "
                     "negative -- no candidate is penalised for them."
                     % ", ".join(missing))
    rb_capable = [f for f in present if f in ("D", "T", "S")]
    if len(rb_capable) < 2:
        lines.append("R1 requires two independent receptor-binding families including a "
                     "direct one (D, T or S). This run has %d, so **R1 is unreachable by "
                     "construction** and every candidate is capped below it. That is a "
                     "property of the evidence available, not of the candidates."
                     % len(rb_capable))
    return lines
