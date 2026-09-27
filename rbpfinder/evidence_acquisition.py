"""M21-a -- who operates the third-party service, the user or RBPfinder.

Not to be confused with `acquisition.py`, which is about acquiring BLAST DATABASES
(M15C). This one is about acquiring EVIDENCE from external analysis services.

One contract for every external provider, because it is one product question. Before this,
each remote tool was on course to invent its own answer: InterPro a submission policy,
HHpred another, AF3 a third.

    MANUAL_HANDOFF     RBPfinder writes a batch; the user runs the service and hands the
                       raw result files back
    AUTOMATED_REMOTE   with authorisation, RBPfinder submits, polls, retrieves
    SKIP               the user has decided this analysis will not happen

The manual path is the mature one. `HHPRED_BATCH.tsv`, the AF3 batches and the manifest
import have worked since M7; automation is newer and more fragile. The defaults say so.

Two things this module exists to hold on to.

**One artefact, one reading.** The modes differ only in who moves the bytes. The same
`.hhr` file must produce the same EvidenceRecord whether a person downloaded it or a
provider did. A provider fetches; it never assigns a family, a direction or a strength.
The acquisition mode is recorded as provenance and is not allowed anywhere near
classification.

**A user who said no is not a user nobody asked.** SKIP reports `DECLINED`, not `NOT RUN`.
"""
import pathlib

from . import return_binding as _return_binding

_BASE = "evidence_acquisition"

MANUAL = "MANUAL_HANDOFF"
AUTO = "AUTOMATED_REMOTE"
SKIP = "SKIP"

# What the command line says -> what the contract calls it.
_CLI_ALIASES = {"manual": MANUAL, "auto": AUTO, "automated": AUTO, "skip": SKIP,
                MANUAL: MANUAL, AUTO: AUTO, SKIP: SKIP}


class UnsupportedMode(Exception):
    """A mode a provider does not offer. Never silently downgraded to one it does."""


def providers(rules):
    return rules.get(_BASE + ".providers")


def spec(provider, rules):
    p = providers(rules).get(provider)
    if p is None:
        raise UnsupportedMode(
            "no acquisition contract for provider %r; known providers are %s"
            % (provider, ", ".join(sorted(providers(rules)))))
    return p


def resolve(provider, requested, rules):
    """Which mode this provider runs in, and why. Returns (mode, reason).

    A mode the provider does not support is refused by name rather than quietly replaced.
    `stage2.remote_policy` already forbids the mirror image of that -- going remote on
    the quiet because a local provider returned nothing -- and a user who asked for
    automation and got a batch file, or asked for manual and had their sequences sent,
    has been told something false either way.
    """
    p = spec(provider, rules)
    supported = list(p["supported_modes"])
    if requested is None:
        return p["default_mode"], "default for %s: %s" % (
            provider, " ".join(str(p.get("why_default", "")).split())[:160])
    want = _CLI_ALIASES.get(str(requested).strip().lower(),
                            _CLI_ALIASES.get(str(requested).strip()))
    if want is None:
        raise UnsupportedMode(
            "unknown evidence mode %r; use one of %s"
            % (requested, ", ".join(rules.get(_BASE + ".cli.global_values"))))
    if want not in supported:
        raise UnsupportedMode(
            "%s does not support %s; it supports %s"
            % (provider, want, ", ".join(supported)))
    return want, "requested"


def resolve_all(rules, global_mode=None, per_provider=None):
    """Resolve every provider at once. Per-provider settings win over the global one."""
    per_provider = per_provider or {}
    out = {}
    for name in providers(rules):
        requested = per_provider.get(name, global_mode)
        if requested is not None and name not in ():
            try:
                mode, why = resolve(name, requested, rules)
            except UnsupportedMode:
                # A global `--evidence-mode auto` must not fail the run because HHpred
                # cannot be automated. It falls back to that provider's default AND says
                # so; an explicit per-provider request still raises.
                if name in per_provider:
                    raise
                mode = spec(name, rules)["default_mode"]
                why = ("%s does not support the run-wide mode %s; using its default %s"
                       % (name, _CLI_ALIASES.get(str(requested).lower(), requested),
                          mode))
        else:
            mode, why = resolve(name, None, rules)
        out[name] = {"mode": mode, "reason": why}
    return out


def run_state_for(provider, mode, rules, ran=None):
    """What the capability matrix should say, given the mode alone.

    Returns None when the mode does not by itself determine the state -- a provider that
    was asked to run has whatever state its actual execution produced.
    """
    if mode == SKIP:
        return rules.get(_BASE + ".skip.run_state")
    return ran


def batch_rows(provider, candidates, by_id, rules):
    """The batch a person can actually act on. Returns (fields, rows).

    The existing HHPRED_BATCH carried a sha256 where the sequence should be, which is a
    checksum, not something anyone can paste into a submission form. Tier and families
    are their own columns rather than prose inside the reason, so the file can be sorted
    and read without parsing sentences.
    """
    p = spec(provider, rules)
    if p.get("manual_batch_file") is None:
        raise UnsupportedMode("%s has no manual batch: %s" % (provider, p["why_default"]))
    fields = list(rules.get(_BASE + ".batch_contract.fields"))
    # One id for the whole batch: it says which request plan a returned file answers.
    # Provenance, never identity -- only the per-row sequence hash can refuse a stale
    # result, and `return_binding.batch_id.is_identity` says so in the rules.
    pairs = [(c["cds_id"],
              _return_binding.sequence_sha256((by_id.get(c["cds_id"]) or {}).get(
                  "translation") or c.get("translation") or ""))
             for c in candidates or []]
    bid = _return_binding.batch_id(provider, pairs)
    rows = []
    for c in candidates or []:
        rec = by_id.get(c["cds_id"]) or {}
        seq = rec.get("translation") or c.get("translation") or ""
        rows.append({
            "candidate_id": c["cds_id"],
            # Bound by id AND the hash of the exact sequence sent. Not by filename: a
            # user who renames a download or submits in a different order would
            # otherwise have their result attached to the wrong protein.
            "sequence_sha256": _return_binding.sequence_sha256(seq) if seq else "",
            "batch_id": bid,
            "sequence": seq,
            "length_aa": rec.get("length_aa") or len(seq),
            "priority": c.get("af3_priority") or "-",
            "current_tier": "%s-%s" % (c.get("tier_r"), c.get("tier_e")),
            "current_evidence_families":
                "+".join(sorted(c.get("independent_families") or [])) or "-",
            "why_requested": c.get("why_requested") or _why(c, provider, rules),
        })
    return fields, rows


def _why(cand, provider, rules):
    """The sentence that justifies someone's time. Names what is missing, not 'more'."""
    fams = sorted(cand.get("independent_families") or [])
    p = spec(provider, rules)
    family = {"hhpred": "D", "interproscan": "D", "foldseek": "T",
              "alphafold": "T", "ebi_blastp": "S"}.get(provider)
    have = "+".join(fams) or "no independent family"
    if family and family not in fams:
        return ("tier %s-%s on %s; %s would add an independent %s and is the cheapest "
                "thing that still can" % (cand.get("tier_r"), cand.get("tier_e"), have,
                                          p.get("service_name", provider), family))
    return ("tier %s-%s on %s; %s would corroborate a reading that currently rests on "
            "one family" % (cand.get("tier_r"), cand.get("tier_e"), have,
                            p.get("service_name", provider)))


def handoff_steps(provider, rules, outdir="."):
    """The numbered instructions for one provider, built from the contract."""
    p = spec(provider, rules)
    batch = p.get("manual_batch_file")
    if batch is None:
        raise UnsupportedMode("%s has no manual mode" % provider)
    kinds = list(p.get("return_kinds") or [])
    steps = ["open %s" % p.get("service_url"),
             "submit the candidates listed in %s (the sequence is in the file)" % batch,
             "download the raw result files"]
    if kinds:
        steps.append("hand the files back to RBPfinder, registered as %s"
                     % " or ".join(kinds))
    else:
        via = p.get("return_via")
        steps.append(
            "note that %s results cannot be imported directly yet: run %s on the "
            "structures, and hand back that result instead"
            % (p.get("service_name", provider), via))
    return {"provider": provider, "service": p.get("service_name"),
            "url": p.get("service_url"), "batch_file": batch, "steps": steps,
            "return_kinds": kinds, "return_loop_closed": p.get("return_loop_closed",
                                                               bool(kinds)),
            "give_us_files_not_descriptions":
                rules.get(_BASE + ".return_contract.rejects")}


def accepts_return(provider, kind, rules):
    """May this artefact kind be handed back for this provider? (bool, reason)."""
    p = spec(provider, rules)
    kinds = list(p.get("return_kinds") or [])
    if kind in kinds:
        return True, "%s is a %s result" % (kind, p.get("service_name", provider))
    if not kinds:
        return False, ("%s results cannot be imported directly; they reach the evidence "
                       "chain through %s" % (p.get("service_name", provider),
                                             p.get("return_via")))
    return False, ("%s is not a %s result; expected %s"
                   % (kind, p.get("service_name", provider), " or ".join(kinds)))


def disclosure(provider_modes, rules):
    """What the user is told before anything is submitted on their behalf.

    Deliberately says the cost is in remote calls, wall-clock time and interaction, and
    deliberately does NOT say automation inherently burns tokens: a deterministic worker
    doing submit/poll/retrieve costs almost nothing, so that claim would be wrong now and
    would date the moment such a worker exists.
    """
    autos = sorted(n for n, m in (provider_modes or {}).items()
                   if (m["mode"] if isinstance(m, dict) else m) == AUTO)
    if not autos:
        return None
    lines = list(rules.get(_BASE + ".automation_disclosure.must_include"))
    return {"providers": autos,
            "headline": "Automated external analysis for: %s" % ", ".join(autos),
            "points": lines,
            "authorisation_reuses": rules.get(
                _BASE + ".automation_disclosure.authorisation.reuse")}


def provenance(mode, provider, rules, artefact_path=None):
    """The block that records HOW a result arrived, kept out of what it means.

    Written into `raw.acquisition`. `convergence.acquisition_mode_is_provenance_not_
    evidence` is the rule; this is the only shape it may take.
    """
    return {"mode": mode, "provider": provider,
            "transmitted_by": rules.get("%s.modes.%s.transmitted_by" % (_BASE, mode),
                                        None),
            "artefact": str(artefact_path) if artefact_path else None,
            "affects_classification": False}


def convergence_pairs(rules):
    """(provider, parser, evidence_builder) for every provider that has both modes.

    The gates use this to assert that a provider's two modes name one parser and one
    builder. If an automated provider ever grew its own reader, the pair would differ
    and there would be two sources of truth for one file.
    """
    out = []
    for name, p in providers(rules).items():
        modes = set(p["supported_modes"])
        if {MANUAL, AUTO} <= modes:
            out.append((name, p.get("parser"), p.get("evidence_builder")))
    return out


def write_batch(provider, fields, rows, outdir, rules):
    """Write one manual batch. An empty batch is a header-only file, never a skip.

    Skipping leaves the PREVIOUS run's list on disk where it reads as this run's request
    -- the same confusion the S records had, one level up. A header with no rows says
    "nothing is owed", which is a result.
    """
    name = spec(provider, rules)["manual_batch_file"]
    path = pathlib.Path(outdir) / name
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(fields) + "\n")
        for r in rows:
            fh.write("\t".join(str(r[f]) for f in fields) + "\n")
    return path


def write_return_template(provider, rows, outdir, rules):
    """The manifest the user hands back, pre-filled except for one column.

    Returns the path, or None when this provider has nothing importable to return -- AF3
    has no manifest kind for a structure, so a template there would invite someone to
    fill in a path nothing can read.
    """
    p = spec(provider, rules)
    kinds = list(p.get("return_kinds") or [])
    batch = p.get("manual_batch_file")
    if not kinds or not batch:
        return None
    tmpl = rules.get(_BASE + ".batch_contract.return_template")
    fields = list(tmpl["fields"])
    name = tmpl["filename_from_batch"].replace(
        "{BATCH}", batch.rsplit(".", 1)[0].replace("_BATCH", ""))
    path = pathlib.Path(outdir) / name
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(fields) + "\n")
        for r in rows:
            row = {"cds_id": r["candidate_id"], "kind": kinds[0], "path": "",
                   "sequence_sha256": r["sequence_sha256"],
                   "batch_id": r.get("batch_id", "")}
            fh.write("\t".join(str(row.get(f, "")) for f in fields) + "\n")
    return path


def issued_batch_ids(outdir, rules):
    """The batch_id in each batch file already sitting in this output directory.

    Read from disk rather than remembered, because the batch that a returned result
    answers was written by an EARLIER run -- usually the previous one, into this same
    directory. There is no state to keep and nothing to get out of sync.
    """
    out = {}
    if not outdir:
        return out
    base = pathlib.Path(outdir)
    for name, p in providers(rules).items():
        batch = p.get("manual_batch_file")
        kinds = list(p.get("return_kinds") or [])
        if not batch or not kinds or not (base / batch).exists():
            continue
        try:
            lines = (base / batch).read_text(encoding="utf-8-sig").splitlines()
            header = lines[0].split("\t")
            if "batch_id" not in header or len(lines) < 2:
                continue
            idx = header.index("batch_id")
            for kind in kinds:
                out[kind] = lines[1].split("\t")[idx]
        except Exception:                                   # noqa: BLE001
            # A batch file we cannot read is not a reason to fail a run. The batch_id is
            # an audit note; the sequence hash is what actually protects anything.
            continue
    return out


def status_for(provider, mode, rules, count=0, auto=None, authorised=True):
    """Where this provider stands. NOT the same question as capability `run_state`.

    `run_state` asks whether evidence arrived. This asks what is happening -- and a
    written batch is a request, so it can never be `completed`.
    """
    if mode == SKIP:
        return "declined"
    p = spec(provider, rules)
    if mode == AUTO:
        # Permitted is not implemented. Asking for automation a build cannot perform must
        # not read as "nothing to do", and must never let the plan claim a transmission
        # that cannot occur.
        if not p.get("automation_implemented"):
            return "no_executor"
        if not authorised:
            return "not_authorised"
        if not count:
            return "not_applicable"
        if auto is None:
            return "not_applicable"
        done, failed = auto.get("successful", 0), auto.get("failed", 0)
        if failed and not done:
            return "failed"
        if failed:
            return "partial"
        return "completed" if done else "not_applicable"
    if not count:
        return "not_applicable"
    if not p.get("return_kinds"):
        # AF3: the batch is a structure REQUEST. What comes back is not something the
        # evidence chain can consume directly, so it is not "awaiting results".
        return "request_generated"
    if p.get("requires_input_from"):
        return "blocked_on_input"
    return "awaiting_user_results"


def plan(candidates_by_provider, rules, global_mode=None, per_provider=None,
         auto_results=None, outdir=None, authorised=True):
    """The Evidence Acquisition Plan: canonical state, computed once.

    Serialised into run_result and rendered by the report; neither re-derives it. The
    capability matrix, the AF3 priority and the Ceiling paragraph all had to be fixed
    for being computed in two places, and this is the same shape of object.

    Passing `outdir` also WRITES the manual batches, so the `batch_path` in the plan is
    a file that exists rather than a filename someone hoped for.
    """
    modes = resolve_all(rules, global_mode, per_provider)
    auto_results = auto_results or {}
    entries = []
    for name in sorted(providers(rules)):
        mode = modes[name]["mode"]
        p = spec(name, rules)
        cands = list(candidates_by_provider.get(name) or [])
        if mode == SKIP:
            # No batch, no transport, no candidates carried forward. The decision is
            # recorded and nothing else happens.
            cands = []
        count = len(cands)
        status = status_for(name, mode, rules, count, auto_results.get(name),
                            authorised=authorised)
        batch_path = None
        return_template = None
        limitations = []
        if mode == MANUAL and p.get("manual_batch_file"):
            external = p.get("batch_writer") == "external"
            if outdir is not None and not external:
                fields, rows = batch_rows(name, cands, _by_id(cands), rules)
                batch_path = str(write_batch(name, fields, rows, outdir, rules).name)
                tmpl = write_return_template(name, rows, outdir, rules)
                if tmpl is not None:
                    return_template = str(tmpl.name)
            else:
                # Either no outdir (planning only) or somebody else owns the file.
                batch_path = p["manual_batch_file"]
            if not p.get("return_kinds"):
                limitations.append(
                    "results cannot be imported directly; they reach the evidence chain "
                    "through %s" % p.get("return_via"))
        if mode == SKIP:
            limitations.append("the user chose not to run this; no batch was written "
                               "and nothing was submitted")
        if status == "not_authorised":
            limitations.append(
                "automated mode was requested for %s and this build implements it, but "
                "the run is not authorised to transmit; nothing was submitted" % name)
        if status == "no_executor":
            limitations.append(
                "automated mode was requested and the contract permits it, but this "
                "build has no implementation for %s; nothing was submitted" % name)
        entries.append({
            "provider": name,
            "service": p.get("service_name"),
            "mode": mode,
            "status": status,
            "candidate_count": count,
            "candidate_ids": [c["cds_id"] if isinstance(c, dict) else c for c in cands],
            "batch_path": batch_path,
            "return_template": return_template,
            "return_kind": (p.get("return_kinds") or [None])[0],
            "parser": p.get("parser"),
            "reason": modes[name]["reason"],
            "limitations": limitations,
            # Only a mode that CAN run transmits anything. A plan that claims a
            # transmission which cannot occur is false in the safe direction, but it is
            # still false, and it is what a user reads before authorising.
            "transmits_sequence": (bool(rules.get(
                "%s.modes.%s.transmits_sequence" % (_BASE, mode)))
                and bool(count)
                and status not in ("no_executor", "declined", "not_authorised")),
        })
    return {"entries": entries,
            "disclosure": disclosure({e["provider"]: e["mode"] for e in entries}, rules),
            "any_transmission": any(e["transmits_sequence"] for e in entries)}


def _by_id(cands):
    return {c["cds_id"]: c for c in cands if isinstance(c, dict)}


def capability_context(plan_obj):
    """What the capability matrix needs from the plan. One place, so it cannot drift."""
    return {e["provider"]: {"mode": e["mode"], "status": e["status"]}
            for e in (plan_obj or {}).get("entries") or []}


def render(plan_obj):
    """The table a user reads. Serialises the plan; derives nothing."""
    entries = (plan_obj or {}).get("entries") or []
    width = max([len(e["service"] or e["provider"]) for e in entries] or [20])
    out = ["Evidence acquisition plan", ""]
    label = {"completed": "completed", "partial": "partially completed",
             "failed": "failed", "awaiting_user_results": "batch generated",
             "request_generated": "request batch generated",
             "blocked_on_input": "waiting for structure input",
             "declined": "no batch written; nothing submitted",
             "no_executor": "requested, but not implemented in this build",
             "not_authorised": "requested, but this run is not authorised to transmit",
             "not_applicable": "nothing requested"}
    for e in entries:
        mode = {"MANUAL_HANDOFF": "MANUAL", "AUTOMATED_REMOTE": "AUTO",
                "SKIP": "DECLINED"}[e["mode"]]
        extra = label.get(e["status"], e["status"])
        if e["candidate_count"] and e["status"] != "completed":
            extra = "%s (%d candidate%s)" % (extra, e["candidate_count"],
                                             "" if e["candidate_count"] == 1 else "s")
        out.append("  %-*s %-9s %s" % (width, e["service"] or e["provider"],
                                       mode, extra))
    return "\n".join(out).rstrip()
