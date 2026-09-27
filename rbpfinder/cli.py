"""RBPfinder executor v0.2.

Scope limits are deliberate and advertised rather than hidden: C6 is not
implemented, and C5 runs only when a comparator cohort is supplied. Stage 6 IS
implemented, as a closed loop -- classify, audit, reopen the pool, reclassify --
so that a candidate missed by every Stage 1 channel can still be recovered.
"""
import argparse
import json
import os
import pathlib
import sys

from .config import Rules
from . import resources as _resources
from .capability import BlastCapability
from . import stage2_local
from . import stage2_remote
from . import annotation as annotation_mod
from . import capability_matrix as capmat
from . import search_state as sstate
from . import return_binding
from . import uniprot_enrichment as uniprot
from . import evidence_acquisition as evacq
from .errors import CapabilityUnavailable, InputValidationError
from .policy import DataPolicy
from . import schema as schema_mod
from .ingest import phold as phold_in
from .ingest import pharokka as pharokka_in
from .ingest import genbank as genbank_in
from .nominate import (c1_context, c2_keywords, c3_dark_matter, c4_cluster,
                       c5_comparative)
from .evidence import normalize
from .evidence import af3_priority
from .evidence import interpro_contract
from .classify import evidence_families, tiers, architecture as arch_mod
from .audit import screening_table, completeness
from .providers import local_files
from .output import candidates as cand_out
from .output import report as report_out
from .output import completeness_statement

_RB = "supports_receptor_binding"

# Manifest kinds that constitute SEQUENCE evidence, and therefore a completed Stage 2
# search. Deliberately a whitelist: an unknown kind is not silently treated as one.
SEQUENCE_MANIFEST_KINDS = ("local_blast_tsv", "remote_blast_tsv", "uniprot_blast_tsv")


def _regions(rec, records):
    counted = evidence_families.counted(records)
    rb = [r for r in counted if r["direction"] == _RB and r["family"] in ("D", "T", "S")]
    if rb:
        src = rb[0]
        return [{
            "region": dict(src["region"]),
            "role": "receptor_binding_domain",
            "evidence": records,
        }]
    return [{
        "region": {"start_aa": 1, "end_aa": max(rec["length_aa"], 1), "source": "manual"},
        "role": "unassigned",
        "evidence": records,
    }]


def _acq_provider_names():
    """Provider names for the per-provider mode flags, read from the contract.

    Built from the rules rather than listed here, so a provider added to the contract
    gets its flag without anyone remembering to add one.
    """
    try:
        return list(Rules(_resources.default_rules_path()).get(
            "evidence_acquisition.providers"))
    except Exception:                                       # noqa: BLE001
        # argparse construction must not be what fails when the rules file is missing;
        # the run itself will fail loudly and with a better message.
        return []


def _acq_candidates(rules, ranked, by_id, remote_prov, enrichment_prov):
    """Who each provider would be asked about. Selection only -- no mode decisions.

    Every rule here already existed somewhere in this file or in the evidence modules;
    M21-b moves them behind one call so the plan can be built once instead of each
    breakpoint deciding for itself. No selection rule is changed.
    """
    def with_seq(cands):
        out = []
        for c in cands:
            rec = by_id.get(c["cds_id"]) or {}
            out.append(dict(c, translation=rec.get("translation") or ""))
        return out

    # Unchanged from the HHPRED_BATCH rule this replaces.
    need_profile = [c for c in ranked
                    if c["tier_r"] in ("R2", "R3")
                    and "D" not in (c.get("independent_families") or [])]
    interpro, _skipped = interpro_contract.select_candidates(with_seq(ranked), rules)
    interpro_ids = {p["cds_id"] for p in interpro}
    return {
        "hhpred": with_seq(need_profile),
        "interproscan": with_seq([c for c in ranked if c["cds_id"] in interpro_ids]),
        # AF3 and Foldseek: the request lists exist, but AF3 keeps its own writer and
        # Foldseek has nothing to search with until structures come back.
        "alphafold": with_seq([c for c in ranked
                               if c.get("af3_priority") in ("P1", "P2")]),
        "foldseek": [],
        "ebi_blastp": [{"cds_id": c} for c in
                       ((remote_prov or {}).get("candidate_ids") or [])],
        "uniprot_enrichment": ([{"cds_id": "enrichment"}]
                               if (enrichment_prov or {}).get("requested") else []),
    }


def _build_acquisition_plan(args, rules, ranked, by_id, remote_prov, enrichment_prov,
                            outdir):
    """The Evidence Acquisition Plan for this run. Computed once, then serialised."""
    per_provider = {}
    for name in _acq_provider_names():
        chosen = getattr(args, "mode_" + name, None)
        if chosen:
            per_provider[name] = chosen
    auto_results = {
        "ebi_blastp": remote_prov or None,
        "uniprot_enrichment": (
            {"successful": (enrichment_prov or {}).get("entries_retrieved", 0),
             "failed": ((enrichment_prov or {}).get("entries_requested", 0)
                        - (enrichment_prov or {}).get("entries_retrieved", 0))}
            if (enrichment_prov or {}).get("requested") else None),
    }
    return evacq.plan(_acq_candidates(rules, ranked, by_id, remote_prov,
                                      enrichment_prov),
                      rules,
                      global_mode=getattr(args, "evidence_mode", None),
                      per_provider=per_provider,
                      auto_results=auto_results,
                      outdir=outdir,
                      # A plan that claims a transmission consent was never given for is
                      # the sentence a user reads before giving it.
                      authorised=bool(getattr(args, "allow_remote", False)))


def _af3_priority(rec, tier, records):
    """Reduced v0.1 rule: information gain, not 'still in doubt'."""
    has_d = any(r["family"] == "D" for r in records)
    if tier["tier_r"] == "N":
        return "P3"
    if not has_d:
        return "P1"
    if tier["tier_r"] in ("R1", "R2"):
        return "P2"
    return "P3"


def run(args):
    rules_path = args.rules or _resources.default_rules_path()
    schema_path = args.schema or _resources.default_schema_path()
    if rules_path is None or schema_path is None:
        print("cannot locate the packaged rules/schema; pass --rules and --schema")
        return 2
    rules = Rules(rules_path)
    sch = schema_mod.load(schema_path)

    # ---- Data policy: decided ONCE, right after Stage 0 ingest, then obeyed ----
    policy = DataPolicy(rules, mode=args.data_policy, run_authorised=args.allow_remote)

    # One resolver for the whole run. Precedence is authority: an explicit path or an
    # environment variable that does not resolve is a failure, never a reason to fall
    # back to whatever PATH offers -- that is how a clean venv on a developer's machine
    # produced a false PASS in M15B-1.
    blast = BlastCapability(blastp=args.blastp, makeblastdb=args.makeblastdb)

    run_flags = []

    # ---- M19-f: a bare FASTA gets guidance, not an annotation pipeline -------
    # RBPfinder
    # states the precondition and names the routes; it does not become the maintainer of
    # someone else's annotation service.
    annotation = None
    genbank_path = args.genbank
    if getattr(args, "fasta", None):
        _fasta_handoff(args, rules)                 # always raises

    if genbank_path:
        cds, ingest_meta = genbank_in.read(
            genbank_path, phage_id=args.phage_id, is_circular=args.circular)
        notes = ingest_meta["warnings"]
        input_mode = ingest_meta["input_mode"]
        gene_caller = ingest_meta["gene_caller"]
        run_flags.append(ingest_meta["structural_screen"])
    else:
        cds = phold_in.read(args.phold)
        notes = []
        if args.pharokka:
            notes = pharokka_in.reconcile(cds, pharokka_in.read(args.pharokka))
        input_mode = "B_raw_fasta"
        gene_caller = args.gene_caller

    c1 = c1_context.run(cds, rules)
    c2 = c2_keywords.run(cds, rules)
    c3 = c3_dark_matter.run(cds, rules, c1["nominated"])
    seeded = c1["nominated"] | c2["nominated"] | c3["nominated"]
    c4 = c4_cluster.run(cds, rules, seeded, is_circular=args.circular)

    # ---- C5, comparative genomics ------------------------------------------
    # The capability now EXISTS in this executor, so "the user gave us nothing to
    # compare against" is a no-comparator state, never a missing-implementation one.
    # Getting this backwards would leave every single-genome run permanently
    # terminated_cleanly=false and train people to ignore the flag.
    if args.comparative_cohort:
        catalog = c5_comparative.load_catalog(
            args.comparative_cohort, args.phage_id,
            lambda p, phage_id: genbank_in.read(p, phage_id=phage_id))
        c5 = c5_comparative.run(cds, catalog, rules,
                                is_circular=args.circular,
                                near_clone_fallback=args.c5_near_clone_fallback,
                                capability=blast)
    else:
        c5 = {"state": c5_comparative.STATE_NO_COHORT, "nominated": set(),
              "loci": [], "details": {}, "presence": {},
              "cohort": {"considered": 0, "chosen": [],
                         "why": "no comparator cohort supplied "
                                "(--comparative-cohort was not given)"}}
    if c5["state"] != c5_comparative.STATE_OK:
        run_flags.append("C5_NO_COMPARATIVE_COHORT")

    channels_by_id = {}
    for tag, res in (("C1", c1), ("C2", c2), ("C3", c3), ("C4", c4)):
        for cid in res["nominated"]:
            channels_by_id.setdefault(cid, set()).add(tag)
    for cid in (c5 or {}).get("nominated", ()):
        channels_by_id.setdefault(cid, set()).add("C5")

    # Test-only hook. Removes a CDS from Stage 1 output so that Stage 6 recovery can
    # be demonstrated on a real genome. Never use outside a rescue fixture.
    suppressed = []
    for cid in args.suppress_nomination or []:
        matched = [k for k in channels_by_id if k.endswith(cid)]
        for k in matched:
            channels_by_id.pop(k)
        suppressed.extend(matched)
        if not matched:
            print("WARNING: --suppress-nomination %s matched nothing" % cid)

    by_id = {r["cds_id"]: r for r in cds}
    state = {"phold_derived": False}

    # ---- Stage 2 providers ---------------------------------------------------
    # Unpublished sequences are never sent anywhere unless a human says so for
    # this run. A blocked remote path is recorded, not silently worked around.
    # ---- Stage 2 input, precedence frozen in M15D -------------------------
    #   1. --stage2-manifest is authoritative replay: no BLAST runs, configured
    #      databases are not consulted at all.
    #   2. otherwise the applicable configured databases are searched, and the
    #      result is turned into exactly the same manifest contract.
    #   3. nothing applicable -> Stage 2 does not run; the core analysis still
    #      completes and the reason is recorded.
    stage2_prov = {"mode": "manifest" if args.stage2_manifest else
                   ("disabled" if args.no_local_stage2 else args.stage2_backend)}
    manifest_path = args.stage2_manifest
    remote = None
    # ---- precedence, re-frozen in M19-d -----------------------------------
    #   manifest only                   -> pure replay, nothing executes
    #   manifest + --stage2-backend ebi  -> the manifest SEEDS the trusted cache and
    #                                       the backend fills the missing delta
    #   checkpoint + backend             -> resume
    #   all three                        -> merge the trusted sources, fill the rest
    #
    # Before M19-d a manifest suppressed the backend entirely, so a candidate Stage 6
    # reopened after the manifest was written could only ever be recorded not_run. There
    # is no new "--search-missing-only" flag: searching only what is missing is what the
    # cache model already means.
    seeds_cache = bool(manifest_path) and args.stage2_backend == "ebi"
    if (not manifest_path or seeds_cache) and not args.no_local_stage2:
        if args.stage2_backend == "ebi":
            # The remote backend searches round by round, so nothing is prepared up
            # front; the session is created here and driven from OUTSIDE the Stage 6
            # loop. An explicit backend choice is never silently replaced by the other
            # one: a remote failure is reported, not quietly answered from a different
            # database snapshot.
            remote = stage2_remote.RemoteStage2Session(
                rules, pathlib.Path(args.out) / "stage2_remote",
                submit=stage2_remote.http_submit(
                    args.ebi_endpoint, email=args.ebi_email,
                    attempts=rules.get("remote_stage2.transport_retry.attempts"),
                    backoff=rules.get("remote_stage2.transport_retry.backoff_seconds")),
                policy=policy, host_context=args.host, host_taxid=args.host_taxid,
                email=args.ebi_email,
                consent=_remote_consent(rules, args.allow_remote))
            # Progress, printed from the SAME summary the JSON reports. The field
            # report described ten minutes of silence, with the user counting files in
            # a second terminal to see whether the process was alive.
            if not args.quiet_progress:
                remote.on_progress = _progress_printer()

            # `--restart-stage2` means "search again", NOT "destroy what happened".
            # The archived state stays on disk beside the new one, and no raw response
            # is touched -- this project already lost 176 MB to a rerun once.
            if args.restart_stage2:
                archived = remote.state.archive("--restart-stage2")
                if archived:
                    sys.stderr.write(
                        "\n--restart-stage2: previous search state archived to %s\n"
                        "Raw responses are left untouched.\n\n" % archived.name)
            elif remote.state.entries:
                # Resume is the DEFAULT. The field report's central complaint was a run
                # that resubmitted forty finished jobs; requiring a flag to avoid that
                # would leave the default behaviour wrong.
                summary = remote.state.summary()
                sys.stderr.write(
                    "\nResuming from %s: %d completed, %d resumable, %d failed.\n"
                    "Only missing searches will be submitted.\n\n"
                    % (remote.state.path.name,
                       summary["searched_hits"] + summary["searched_no_hit"],
                       summary["resumable"], summary["failed"]))


            if seeds_cache:
                seeded, conflicts = _seed_cache_from_manifest(
                    remote.state, local_files.Manifest(manifest_path), by_id)
                if conflicts:
                    raise InputValidationError(
                        "the manifest and the saved search state disagree:\n  %s\n"
                        "Two trusted sources cannot both be right; resolve this "
                        "rather than letting one silently win."
                        % ("\n  ".join(conflicts)))
                sys.stderr.write(
                    "Seeded %d trusted result(s) from %s; the backend will fill "
                    "the rest.\n\n" % (seeded, pathlib.Path(manifest_path).name))
                manifest_path = None      # consumed as a cache, not as a replay
        else:
            manifest_path, auto_prov = stage2_local.prepare(
                cds, rules, blast, args.out, host_context=args.host)
            stage2_prov.update(auto_prov)
    manifest = local_files.Manifest(manifest_path)

    # ---- M21-c: a returned result must belong to the sequence it was requested for --
    # Checked here, before a single file reaches a parser. A row that fails is REMOVED
    # from the manifest rather than flagged, because `collect()` reads the manifest and
    # would parse anything left in it whatever a verdict elsewhere said.
    # The batch a returned result answers was written by an EARLIER run, usually into
    # this same directory. Read from disk rather than remembered: there is no state to
    # keep in sync, and a batch file we cannot read is not a reason to fail a run.
    binding = return_binding.check(manifest.rows, by_id, manifest.bound_schema(), rules,
                                   issued=evacq.issued_batch_ids(args.out, rules))
    for v in binding["rejected"]:
        manifest.drop(v["cds_id"], v["kind"])

    def remote_delta(pool_ids):
        """Search whatever the current pool has not been searched for yet.

        Called before every classify pass, never from inside the audit: Stage 6 decides
        WHICH candidates to reopen and stays offline-replayable, while the network work
        happens one level up.
        """
        if remote is None:
            return
        for cid, path in remote.ensure_searched(by_id, pool_ids).items():
            manifest.add(cid, "remote_blast_tsv", path)
    run_flags.extend(policy.run_flags())
    stage2_extras = {}
    stage2_required = []

    records_by_id = {}

    def classify(pool, chans_map):
        cands, tiers_by_id, families_by_id = [], {}, {}
        for cid in sorted(pool):
            rec = by_id[cid]
            chans = sorted(chans_map[cid])
            records = normalize.build(rec, set(chans), rules, c2["matches"],
                                      (c5 or {}).get("details"))
            s2_records, s2_extra = local_files.collect(rec, manifest, rules, policy)
            records.extend(s2_records)
            if s2_extra:
                stage2_extras[cid] = s2_extra
            fams, meta = evidence_families.decorrelate(records, rules)
            state["phold_derived"] = (
                state["phold_derived"] or meta["phold_hit_is_phrog_derived"]
            )
            t = tiers.assign(records, fams, rules)
            role, layer = tiers.role_and_layer(t, records, rules)
            loc = tiers.rb_localisation(records)
            tiers_by_id[cid] = t
            families_by_id[cid] = fams
            records_by_id[cid] = records
            cands.append({
                "cds_id": cid,
                "locus": {
                    "start_nt": rec["start_nt"], "end_nt": rec["end_nt"],
                    "strand": rec["strand"], "length_aa": rec["length_aa"],
                    "spans_origin": rec.get("spans_origin", False),
                },
                "annotation": {
                    "product": rec["product"], "phrog": rec["phrog"],
                    "function_category": rec["function"],
                },
                "nomination_channels": chans,
                "regions": _regions(rec, records),
                "independent_families": fams,
                "tier_r": t["tier_r"],
                "tier_e": t["tier_e"],
                "protein_role": role,
                "definition_layer": layer,
                "rbp_evidence_localisation": loc if layer == "RBP_core" else None,
                "downgrade": (
                    {"level": "hard_exclusion",
                     "positive_counter_evidence": ["annotator assigns a non-RBP identity"]}
                    if t["hard_contradiction"] else None
                ),
                "af3_priority": _af3_priority(rec, t, records),
                "_rb_families": t["rb_families"],
                "_max_rb_strength_rank": t["max_rb_strength_rank"],
            })
        return cands, tiers_by_id, families_by_id

    # ---- Stage 6 closed loop: classify -> audit -> reopen -> reclassify ----
    pool = set(channels_by_id)
    cap = rules.get("stage6.reopen.max_cycles")
    acted, all_hits, outcomes = set(), [], {}
    cycles, cap_reached = 0, False

    remote_delta(pool)                      # round 0: the initial candidate pool
    cands, tiers_by_id, families_by_id = classify(pool, channels_by_id)

    while True:
        hits, exec_state = completeness.run_cycle(
            cds, pool, cands, rules, is_circular=args.circular, c5_result=c5
        )
        fresh = [h for h in hits
                 if (h["audit_id"], h["cds_id"], h["reason_class"]) not in acted]
        known = {(h["audit_id"], h["cds_id"], h["reason_class"]) for h in all_hits}
        for h in hits:
            if (h["audit_id"], h["cds_id"], h["reason_class"]) not in known:
                all_hits.append(h)
                known.add((h["audit_id"], h["cds_id"], h["reason_class"]))
        if not fresh:
            break
        if cycles >= cap:
            cap_reached = True
            for h in fresh:
                outcomes[(h["audit_id"], h["cds_id"])] = "documented_unresolved"
                h["written_reason"] = "REOPEN_CAP_REACHED; " + h["why_flagged"]
            break
        for h in fresh:
            acted.add((h["audit_id"], h["cds_id"], h["reason_class"]))
            channels_by_id.setdefault(h["cds_id"], set()).add("S6-" + h["audit_id"])
            pool.add(h["cds_id"])
        cycles += 1
        remote_delta(pool)                  # only the CDS Stage 6 just reopened
        cands, tiers_by_id, families_by_id = classify(pool, channels_by_id)
        for h in fresh:
            t = tiers_by_id[h["cds_id"]]
            outcomes[(h["audit_id"], h["cds_id"])] = (
                "reopened_and_promoted" if t["tier_r"] in ("R1", "R2")
                else "reopened_and_downgraded"
            )
            h["written_reason"] = "%s; after reopen tier=%s-%s" % (
                h["why_flagged"], t["tier_r"], t["tier_e"])

    audit_result = completeness.build_result(
        all_hits, exec_state, outcomes, cycles, cap_reached, rules
    )

    if c1.get("warning"):
        run_flags.append(c1["warning"])
    for w in c4.get("warnings", []):
        run_flags.append(w.split(":")[0])

    ranked = cand_out.rank(cands, rules)
    primary, rescue = cand_out.assign_sets(ranked, rules)

    screening = screening_table.build(
        cds, pool, tiers_by_id, channels_by_id, families_by_id, rules, c1, c2["matches"]
    )

    # Stage 0 typing is a soft prior. It reaches k_default and the priority loci and
    # nothing else -- there is no code path from architecture to nomination, so a wrong
    # prior costs ranking quality, never reachability.
    arch, arch_meta = arch_mod.resolve(cds, rules, args.architecture)
    k = rules.get("stage0.architectures.%s.k_default" % arch)

    disclaimers = [
        "C1 anchor vocabulary and C4 gap threshold are uncalibrated (calibration: pending).",
        ("Architecture came from the command line; the automatic Stage 0 prior was %s "
         "(%s)." % (arch_meta["auto_prediction"], arch_meta["auto_confidence"])
         if args.architecture else
         "Architecture is an AUTOMATIC Stage 0 prior (%s, confidence %s) and is used only "
         "for window priority and k_default." % (arch, arch_meta["auto_confidence"])),
        "AF3 priority uses a reduced v0.1 rule; the full information-gain triggers are not implemented.",
    ]
    if suppressed:
        disclaimers.insert(0, "TEST FIXTURE: Stage 1 nomination was artificially suppressed "
                              "for %s." % ", ".join(sorted(suppressed)))

    # ---- run status ---------------------------------------------------------
    # Reported in the field: with 40 submissions, 0 successful and 40 transport
    # failures, the CLI still printed a plain `OK`. A user reasonably reads that as
    # "Stage 2 ran and found nothing", which is the one reading the whole
    # searched-no-hit / not-run distinction exists to prevent. The searches were never
    # completed; the status has to say so.
    remote_prov = remote.provenance() if remote else None

    # ---- M19-e: the exit code reflects what the run ACHIEVED ----------------
    # Asking for `--stage2-backend ebi` makes remote Stage 2 a required capability for
    # this run. 0 completed out of 40 attempted is a failure, and exit 0 would tell a
    # shell, a batch script and an agent that the requested operation succeeded.
    #
    # "Completed" is read from the persistent search state, not from what this process
    # happened to submit -- a run that reuses 39 cached results and fails its one new
    # search has not failed Stage 2. And searched_no_hit is completed: not a hit, but a
    # finished search.
    #
    # Three statuses, not one bucket called `failed`: a partial run and a total failure
    # are different things, and the process exit code is a fourth thing again.
    # This is deliberately NOT written into `run_status`. That field already answers a
    # different question -- did the executor finish, `completed | failed`, schema-bound
    # -- and M14A exists precisely because three questions were once collapsed into one
    # flag. "The program finished" and "the remote arm achieved nothing" are both true
    # of the same run, and a reader needs both.
    explicit_remote = args.stage2_backend == "ebi"
    remote_required = int((remote_prov or {}).get("required_searches") or 0)
    remote_done = int((remote_prov or {}).get("effective_completed") or 0)
    remote_failed = int((remote_prov or {}).get("failed") or 0)

    if not remote_prov:
        stage2_remote_status, degraded, remote_fatal = "not_requested", False, False
    elif remote_required > 0 and remote_done == 0:
        stage2_remote_status, degraded = "failed_remote_stage2", True
        # Only an explicit request makes this fatal. As an optional enhancement, a
        # failed remote arm still leaves a usable analysis -- degraded, never silent.
        remote_fatal = explicit_remote
    elif remote_failed:
        stage2_remote_status, degraded, remote_fatal = (
            "degraded_remote_stage2_partial", True, False)
    else:
        stage2_remote_status, degraded, remote_fatal = "completed", False, False

    run_result = {
        "stage2_remote_status": stage2_remote_status,
        "phage_id": args.phage_id,
        "input_mode": input_mode,
        "gene_caller": gene_caller,
        "architecture": arch,
        "architecture_is_prior_only": True,
        "architecture_typing": arch_meta,
        "k_default": k,
        "candidates": [
            {k2: v for k2, v in c.items() if not k2.startswith("_")} for c in ranked
        ],
        "primary_set": primary,
        "rescue_set": rescue,
        "screening": screening,
        "completeness_audit": audit_result,
        # Present only when the run started from a FASTA. Absent means the GenBank
        # was supplied, which is a different provenance claim from "annotation ran and
        # produced nothing".
        "annotation": annotation.provenance() if annotation else None,
        "uniprot_enrichment": None,        # filled in below, after the candidates exist
        "stage2_local": stage2_prov,
        "stage2_remote": (remote.provenance() if remote else None),
        "comparative_genomics": ({
            "state": c5["state"],
            "cohort": c5["cohort"],
            "variable_loci": c5["loci"],
        } if c5 else {"state": c5_comparative.STATE_NOT_RUN}),
        "run_flags": run_flags,
        "heuristic_disclaimers": disclaimers,
    }

    # ---- product-level statement: what the USER is told, separate from the audit's
    # internal booleans. "The program finished" and "the biology is settled" are
    # different claims and used to share one flag.
    status, assessment, coverage, limitations, facts = completeness_statement.build(
        run_result, ranked, records_by_id, rules,
        c5_state=(c5 or {}).get("state"),
        c5_loci=(c5 or {}).get("loci"),
        shortlist=set(primary) | set(rescue),
        cds_order=[r["cds_id"] for r in sorted(cds, key=lambda x: (x["start_nt"],
                                                                  x["cds_id"]))],
        extra_limitations=(remote.limitations if remote else ()))
    run_result["run_status"] = status
    run_result["completeness_assessment"] = assessment
    run_result["assessment_coverage"] = coverage
    run_result["limitations"] = limitations
    run_result["completeness_facts"] = facts

    # ---- presentation only: when the evidence cannot produce a shortlist, do not hand
    # the user a blank page. These stay R3, stay out of rescue, and are excluded from
    # every recall metric -- see product_contract.presentation_policy.
    _ep = "product_contract.presentation_policy.exploratory_candidates"
    shown = set(primary) | set(rescue)
    trigger = None
    if not shown:
        trigger = "primary_and_rescue_empty"
    elif assessment == "limited":
        trigger = "limited_with_nonempty_shortlist"
    if trigger:
        n = min(rules.get(_ep + ".n_default"), rules.get(_ep + ".n_max"))
        run_result["top_unresolved_candidates"] = {
            "trigger": trigger,
            "display": rules.get(_ep + ".triggers." + trigger),
            "note": "shown because the evidence was insufficient to build a confident "
                    "shortlist; NOT promoted to rescue and NOT counted in any recall",
            "candidates": [
                {"cds_id": cnd["cds_id"], "rank": i + 1,
                 "tier": "%s-%s" % (cnd["tier_r"], cnd["tier_e"]),
                 "families": cnd.get("independent_families") or [],
                 "product": cnd["annotation"]["product"]}
                for i, cnd in enumerate(ranked) if cnd["cds_id"] not in shown][:n],
        }

    # ---- AF3 priority, computed once, before ANY file is written ------------
    # Reported in the field: the same candidate was P3 in run_result.json and P1 in both
    # RBP_candidates.tsv and AF3_MONOMER_BATCH.tsv. Two rules were computing it -- the
    # reduced v0.1 rule in `_af3_priority()` above, and the real
    # `stage3.af3_priority` rule inside `af3_priority.manifest_rows()`, which overwrites
    # `cand["af3_priority"]`. The JSON was serialised BEFORE that overwrite and every
    # other file after it, so the value a reader got depended on which file they opened.
    #
    # `manifest_rows()` is authoritative; it is the one that reads the frozen rule. So
    # it runs first and every writer serialises what it left behind.
    af3_fields, af3_rows = af3_priority.manifest_rows(
        ranked, by_id, records_by_id, rules, k)
    _prio = {c["cds_id"]: c.get("af3_priority") for c in ranked}
    for entry in run_result.get("candidates", []):
        if entry.get("cds_id") in _prio:
            entry["af3_priority"] = _prio[entry["cds_id"]]

    outdir = pathlib.Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    # ---- M20-a2: explain the Stage 2 hits, without changing them -------------
    # A non-blocking enhancement, deliberately unlike Stage 2 itself. Stage 2 IS evidence
    # acquisition and its failure is a real degradation; this only describes what those
    # hits are. A UniProt outage must not cost the user their analysis, so it can never
    # raise, never alter an existing S record and never reach the exit code.
    enrichment_prov = _enrich_uniprot(rules, run_result["candidates"], remote_prov,
                                      stage2_extras)

    # ---- M21-b: the Evidence Acquisition Plan, computed once -----------------
    # Canonical state, like the capability matrix and the AF3 priority -- both of which
    # had to be fixed for being computed twice. The plan decides the mode per provider,
    # writes the manual batches, and is then serialised. Nothing downstream re-derives it.
    acq_plan = _build_acquisition_plan(args, rules, ranked, by_id, remote_prov,
                                       enrichment_prov, outdir)

    # ---- capability matrix, ONE canonical answer for every consumer ----------
    # Built from the candidates' own EvidenceRecords. The report, the JSON and stdout
    # all serialise this; none of them re-derives it.
    cap_context = {
        "annotated_input": bool(genbank_path),
        "c5_state": ("ok" if c5["state"] == c5_comparative.STATE_OK
                     else ("no_cohort" if c5["state"] == c5_comparative.STATE_NO_COHORT
                           else "not_run")),
        "remote": remote_prov,
    }
    cap_context["uniprot_enrichment"] = enrichment_prov
    cap_context["acquisition_modes"] = evacq.capability_context(acq_plan)
    cap_rows = capmat.build(rules, run_result["candidates"], cap_context)
    fams_collected = capmat.families_collected(run_result["candidates"])
    run_result["uniprot_enrichment"] = enrichment_prov
    run_result["evidence_acquisition_plan"] = acq_plan
    run_result["return_binding"] = {k: binding[k] for k in
                                    ("schema", "counts", "verdicts", "rejected",
                                     "same_sequence_reuse", "batch_notes",
                                     "legacy_notice")}
    run_result["capability_matrix"] = [
        {k: v for k, v in row.items() if k != "note"} for row in cap_rows]

    errors = schema_mod.validate(run_result, sch)
    if errors:
        (outdir / "SCHEMA_ERRORS.txt").write_text("\n".join(errors), encoding="utf-8")
        print("SCHEMA VALIDATION FAILED (%d errors) -- no report written" % len(errors))
        for e in errors[:20]:
            print("  " + e)
        return 2

    if stage2_extras:
        (outdir / "stage2_homolog_consensus.json").write_text(
            json.dumps(stage2_extras, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8")
    (outdir / "run_result.json").write_text(
        json.dumps(run_result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    screening_table.write_tsv(screening, outdir / "all_CDS_screening.tsv")

    # HHPRED_BATCH and INTERPRO_BATCH are written by the acquisition plan above, under
    # the M21-a batch contract: the sequence itself rather than a checksum of it, tier
    # and families as their own columns, and a candidate_id + sequence_sha256 pair that
    # binds a returned file to the request it answers.
    #
    # AF3_MONOMER_BATCH keeps its own writer and its own columns, below.
    # `scripts/benchmark_stage2_48.py` and `scripts/m7c_pipeline_metrics.py` both read
    # that file by column name, so reshaping it is a separate decision from wiring the
    # modes, and M21-b is not the place to make it.
    # already computed above, before serialisation -- deliberately not recomputed
    # STAGE2_REQUIRED is a different breakpoint from an AF3 request: the cheap search
    # has not run yet, and ordering a structure first would invert the cost funnel.
    stage2_required = [r for r in af3_rows if r["priority"] == "STAGE2_FIRST"]
    af3_only = [r for r in af3_rows if r["priority"] != "STAGE2_FIRST"]
    # An empty batch is written as a header-only file rather than skipped. Skipping
    # leaves the PREVIOUS run's list on disk, where it reads as this run's request --
    # the same never-looked/looked-and-found-nothing confusion the S records had, one
    # level up. A header with no rows says "nothing is owed", which is a result.
    # These two keep their own writer and columns, but not their own opinion about
    # whether to run: a provider the user declined gets no batch, per
    # `evidence_acquisition.declined_effects.write_batch`. Without this the plan says
    # DECLINED while the request list sits in the output directory contradicting it.
    _declined = {e["provider"] for e in acq_plan["entries"] if e["mode"] == "SKIP"}
    for fname, rows_, _owner in (("AF3_MONOMER_BATCH.tsv", af3_only, "alphafold"),
                                 ("STAGE2_REQUIRED.tsv", stage2_required, "ebi_blastp")):
        if _owner in _declined:
            continue
        with (outdir / fname).open("w", encoding="utf-8", newline="") as fh:
            fh.write("\t".join(af3_fields) + "\n")
            for r in rows_:
                fh.write("\t".join(str(r[f]) for f in af3_fields) + "\n")

    cand_out.write_tsv(ranked, outdir / "RBP_candidates.tsv")
    report_out.write(
        run_result, ranked, screening, outdir / "RUN_REPORT.md",
        {"c1": c1, "c4": c4, "ingest_notes": notes, "policy": policy,
         "phold_phrog_derived": state["phold_derived"],
         "capability_rows": cap_rows,
         "ceiling_lines": capmat.ceiling_paragraph(cap_rows, fams_collected)},
    )
    for line in policy.summary_lines():
        print("    %s" % line)
    for line in return_binding.summary_lines(binding):
        print(line)
    plan_text = evacq.render(acq_plan)
    if plan_text:
        print()
        print(plan_text)
    if degraded:
        # First, and before the shortlist, because the shortlist is what a user acts on.
        label = ("FAILED  " if stage2_remote_status == "failed_remote_stage2"
                 else "DEGRADED")
        print("%s  remote Stage 2: %d of %d searches completed, %d failed."
              % (label, remote_done, remote_required, remote_failed))
        print("          A failed search is NOT a zero-hit result. Those candidates "
              "carry no")
        print("          sequence evidence and are recorded not_run.")
        if remote_fatal:
            print("          --stage2-backend ebi was requested explicitly and nothing "
                  "completed;")
            print("          exiting 3. Results and checkpoints are left untouched, so "
                  "the next")
            print("          run resumes rather than starting over.")
    print("%s  primary %d | rescue %d | completeness %s | coverage %s"
          % ("OK " if not degraded else "   ",
             len(primary), len(rescue), assessment, coverage))
    if remote_prov:
        # Printed from the same provenance the JSON serialises. The 39-vs-40 split
        # happened because two places counted the same thing separately.
        print("    stage2 remote: %d of %d completed / %d failed / %d reused from cache"
              % (remote_done, remote_required, remote_failed,
                 remote_prov.get("reused_from_prior_state", 0)))
    print("    unresolved: %d sequence-layer candidate(s), longest dark run %d CDS, %d loci"
          % (facts["sequence_layer_unresolved_candidates"],
             facts["longest_unresolved_tail_run_cds"], facts["unresolved_loci"]))
    for lim in limitations:
        print("    limitation [%s/%s]: %s" % (lim["kind"], lim["severity"] or "-", lim["text"]))
    print("    internal pool %d of %d CDS (recall protection, not the shortlist)"
          % (len(pool), len(cds)))
    print("    stage6: reopen_cycles=%d  terminated_cleanly=%s  hits=%d"
          % (audit_result["reopen_cycles"], audit_result["terminated_cleanly"],
             len(all_hits)))
    print("    schema: PASS   outputs: %s" % outdir)
    # Every artefact above has already been written, and nothing is removed on the way
    # out: exit 3 says "the capability you asked for did not deliver", not "throw this
    # away". The checkpoint is intact, so the next run resumes.
    if remote_fatal:
        return 3
    return 0


def _galaxy_params(args):
    """Only parameters that change what the annotation MEANS.

    They feed `annotation_config_sha256`, so anything added here correctly invalidates a
    cached annotation. Anything left out would let a changed setting reuse an old result.
    """
    params = {}
    if getattr(args, "galaxy_gene_predictor", None):
        params["gene_predictor"] = args.galaxy_gene_predictor
    if getattr(args, "galaxy_meta", False):
        params["meta"] = True
    return params


def _annotation_consent(rules, allow_remote_annotation):
    """UNUSED BY THE DEFAULT PATH -- see `_fasta_handoff`. Kept as developer-only
    infrastructure for the experimental annotation providers.

    Consent for transmitting a WHOLE GENOME. Separate from the Stage 2 gate.

    Stage 2 sends selected candidate proteins; this sends the entire genome. The two
    differ in kind, so `--allow-remote` deliberately does not satisfy this and this
    deliberately does not satisfy `--allow-remote`. Inferring either from the other would
    mean a user who agreed to submit a handful of proteins had, without being asked,
    agreed to publish the genome.
    """
    def ask(notice):
        sys.stderr.write("\n" + notice.rstrip() + "\n")
        if allow_remote_annotation:
            sys.stderr.write(
                "Authorised for this run by --allow-remote-annotation.\n\n")
            return True
        sys.stderr.write(
            "\nRemote annotation was not authorised, so the genome has not been sent. "
            "Pass --allow-remote-annotation to authorise it for this run.\n"
            "(--allow-remote covers Stage 2 candidate submission only; it does not "
            "authorise transmitting the genome.)\n\n")
        return False
    return ask


def _enrich_uniprot(rules, candidates, remote_prov, stage2_extras):
    """Attach UniProt context to the S records Stage 2 produced. Never raises.

    Returns a provenance block that says what was asked for and what came back, so
    "we looked and UniProt had nothing" stays distinguishable from "we never looked".
    """
    prov = {"requested": False, "completed": False, "entries_requested": 0,
            "entries_retrieved": 0, "database_release": None, "limitation": None,
            "adds_independent_family": False}
    if not remote_prov:
        # Nothing was searched remotely, so there are no accessions to explain.
        prov["limitation"] = "no remote Stage 2 hits to enrich"
        return prov

    prov["requested"] = True
    limit = rules.get("uniprot_enrichment.max_accessions_per_record")
    prov["max_accessions_per_record"] = limit
    enricher = uniprot.UniProtEnricher(rules)
    requested = retrieved = 0
    try:
        for cand in candidates or []:
            for region in cand.get("regions") or []:
                for rec in region.get("evidence") or []:
                    if rec.get("family") != "S" or rec.get("not_run"):
                        continue
                    hits = _hits_of(rec, stage2_extras.get(cand["cds_id"]), limit)
                    if not hits:
                        continue
                    enricher.enrich_record(rec, hits)
                    # Counted from what was written to the record, not from a counter
                    # kept alongside it. One fact, one computation.
                    enr = (rec.get("raw") or {}).get("uniprot_enrichment") or {}
                    requested += enr.get("accessions_requested", 0)
                    retrieved += enr.get("accessions_enriched", 0)
    except Exception as exc:                                # noqa: BLE001
        # Blanket, and deliberately so: this is an enhancement. Whatever went wrong,
        # the analysis that already succeeded is not going to be lost over it.
        prov["limitation"] = ("UniProt enrichment stopped early: %s. The Stage 2 "
                              "evidence is unaffected." % str(exc)[:160])

    prov["entries_requested"] = requested
    prov["entries_retrieved"] = retrieved
    releases = {e.get("database_release") for e in enricher._cache.values()}
    prov["database_release"] = sorted(r for r in releases if r)[:1] or None
    prov["database_release"] = (prov["database_release"] or [None])[0]
    prov["completed"] = bool(requested) and retrieved == requested
    if enricher.failures and not prov["limitation"]:
        prov["limitation"] = ("%d UniProt lookup(s) did not complete; the Stage 2 "
                              "evidence they would have described is unchanged."
                              % len(enricher.failures))
    return prov


def _hits_of(s_record, consensus, limit):
    """The top few accessions behind one S record.

    `consensus.per_hit` is already ranked, so the first entries are the ones carrying the
    reading. The record's own `raw.hit_id` is the fallback when no consensus was stored.
    """
    accs = []
    for entry in ((consensus or {}).get("per_hit") or [])[:limit]:
        acc = entry.get("accession")
        if acc:
            accs.append(acc)
    if not accs:
        top = (s_record.get("raw") or {}).get("hit_id")
        if top:
            accs.append(top)
    return [{"accession": a} for a in accs]


def _seed_cache_from_manifest(state, manifest, by_id):
    """A manifest is a source of trusted completions, not a separate science path.

    Only SEQUENCE-evidence rows count. A manifest routinely also indexes hhpred, interpro
    and foldseek artefacts, and importing one of those as a completed Stage 2 search
    would claim a sequence search happened because a domain result exists -- the same
    misattribution the capability matrix had to be rebuilt to avoid.

    A row says a search happened for that CDS; a zero-byte artefact says it happened and
    found nothing. A CDS with NO sequence row is not trusted at all: that is the
    never-searched state.
    """
    seeded, conflicts, skipped = 0, [], 0
    for cds_id, kinds in manifest.entries.items():
        rec = by_id.get(cds_id)
        if not rec or not rec.get("translation"):
            continue
        seq_paths = [path for kind, path in (kinds or {}).items()
                     if kind in SEQUENCE_MANIFEST_KINDS]
        if not seq_paths:
            skipped += 1
            continue
        # Non-empty artefact -> hits; zero bytes -> searched and found nothing. A
        # MISSING file is neither, and the Manifest contract already raises on it.
        has_hits = any(pathlib.Path(p).exists() and pathlib.Path(p).stat().st_size > 0
                       for p in seq_paths)
        sha = sstate.sequence_sha256(rec["translation"])
        try:
            state.import_manifest(cds_id, sha, has_hits)
            seeded += 1
        except sstate.TrustConflict as exc:
            conflicts.append(str(exc))
    return seeded, conflicts


def _fasta_handoff(args, rules):
    """A bare FASTA is an input-guidance case, not the start of an analysis.

    RBPfinder deliberately does NOT annotate. Owning a remote annotation provider would
    mean owning third-party accounts, terms of service, rate limits, upload lifecycles,
    provider version drift and data-retention disclosure -- none of which is what this
    tool is good at, and all of which would sit between the user and the RBP work.

    Annotation is a precondition. RBPfinder states the precondition, names the routes,
    and says what to bring back. Nothing is transmitted.
    """
    fasta = pathlib.Path(args.fasta)
    if not annotation_mod.looks_like_bare_fasta(fasta):
        raise InputValidationError(
            "%s does not look like a nucleotide FASTA. If it is already annotated, "
            "pass it with --genbank instead." % fasta.name)

    out = args.out or "results"
    lines = [
        "FASTA input detected: %s" % fasta.name,
        "",
        "RBPfinder requires an annotated GenBank containing CDS features and",
        "protein translations before RBP analysis can begin. It does not annotate",
        "genomes itself.",
        "",
        "Recommended:",
        "  - Online: annotate the genome at https://phage-annotation.org",
        "            (pharokka + phold + phynteny; no account, no API key)",
        "  - Local:  run Pharokka  --  https://github.com/gbouras13/pharokka",
        "  - Then provide the resulting .gbk/.gbff file to RBPfinder.",
        "",
        # The sentence that stops a user assuming the file was quietly uploaded the
        # moment they passed it in.
        "No genome data has been transmitted by RBPfinder.",
        "",
        "After annotation:",
        "",
        "  rbpfinder \\",
        "    --genbank annotated_phage.gbk \\",
        "    --phage-id %s \\" % (args.phage_id or "MyPhage"),
        "    --stage2-backend ebi \\",
        "    --allow-remote \\",
        "    --ebi-email you@your-institution.example \\",
        "    --out %s" % out,
        "",
        "Remote Stage 2 above IS RBPfinder's own work: it gathers sequence evidence",
        "for candidate proteins. Annotation is not.",
    ]
    raise InputValidationError(chr(10).join(lines))


def _progress_printer():
    """One line per state change, plus a running tally.

    Deliberately never prints an EBI `/status` response. The point is that neither a
    person nor a language model should have to read the network protocol to know how
    far along the run is -- reading forty jobs' worth of polling responses is how the
    context window gets spent on transport instead of biology.
    """
    seen = {"n": 0}

    def show(log):
        s = log.summary()
        done = s["completed_hits"] + s["completed_no_hit"] + s["failed"]
        if done == seen["n"]:
            return                      # a submission, not a completion; stay quiet
        seen["n"] = done
        sys.stderr.write("    [%d/%d] %s\n" % (done, s["total"], log.progress_line()))
        sys.stderr.flush()
    return show


def _remote_consent(rules, allow_remote):
    """The consent gate for `--stage2-backend ebi`, as a callable the session invokes.

    On the CLI there is no y/N prompt, and that is deliberate. `--allow-remote` is
    already the per-run authorisation the data policy requires, so a prompt could only
    ever be a second question whose "yes" still would not be enough -- the user would
    agree, then be refused for the missing flag. One authorisation, stated once.

    What the flag does NOT do is silence the notice. It authorises; someone reading the
    log afterwards must still be able to see exactly what was disclosed before the first
    sequence left. The y/N wording stays in YAML for the GUI, which has a real dialog to
    put it in.
    """
    def ask(notice):
        sys.stderr.write("\n" + notice.rstrip() + "\n")
        if allow_remote:
            sys.stderr.write("Authorised for this run by --allow-remote.\n\n")
            return True
        sys.stderr.write(
            "\nRemote submission was not authorised, so nothing has been sent. "
            "Pass --allow-remote to authorise it for this run.\n\n")
        return False

    return ask


def main(argv=None):
    # `doctor` is dispatched before the analysis parser sees anything, so the run flags
    # stay exactly as they were and a diagnostic command never has to satisfy them.
    argv_list = list(sys.argv[1:] if argv is None else argv)
    if argv_list and argv_list[0] == "doctor":
        from . import doctor as _doctor
        return _doctor.main(argv_list[1:])
    if argv_list and argv_list[0] == "database":
        from . import database_cli as _db
        try:
            return _db.main(argv_list[1:])
        except InputValidationError as exc:
            sys.stderr.write("Input error: %s\n" % exc)
            return exc.exit_code
    # M22-d. `bulk` re-invokes this same `main()` once per genome in a manifest, so it
    # is dispatched exactly like `database` -- before the single-genome `run` parser sees
    # anything, and lazily imported so a plain `rbpfinder run` never pays for it.
    if argv_list and argv_list[0] == "bulk":
        from . import bulk_cli as _bulk
        try:
            return _bulk.main(argv_list[1:])
        except InputValidationError as exc:
            sys.stderr.write("Input error: %s\n" % exc)
            return exc.exit_code

    p = argparse.ArgumentParser(prog="rbpfinder")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--phold", help="Mode B: phold per-CDS TSV from the cloud route")
    src.add_argument("--genbank", help="Mode A: a pharokka-annotated GenBank file")
    src.add_argument("--fasta",
                     help="a bare genome FASTA. RBPfinder does not annotate genomes: "
                          "this prints how to obtain an annotated GenBank and exits "
                          "without transmitting anything")
    p.add_argument("--pharokka")
    # Optional since M15B: an installed package carries its own rules and schema, so a
    # user never has to point at a checked-out repository.
    p.add_argument("--rules", help="override the packaged decision_rules.yaml")
    p.add_argument("--schema", help="override the packaged evidence_schema.json")
    p.add_argument("--out", required=True)
    p.add_argument("--phage-id", required=True)
    p.add_argument("--architecture",
                   help="explicit override of the automatic Stage 0 prior. Omit it to let "
                        "the typer decide; the run records auto_prediction, user_override "
                        "and override_used either way")
    p.add_argument("--gene-caller", default="phanotate")
    p.add_argument("--circular", action="store_true")
    p.add_argument("--comparative-cohort",
                   help="directory of annotated GenBanks to use as C5 comparators. "
                        "Comparators are chosen by shared gene content; supplying RBP "
                        "labels or benchmark clusters here is forbidden by the YAML")
    p.add_argument("--c5-near-clone-fallback", action="store_true",
                   help="EXPERIMENTAL C5 v1.1: when the primary shared-gene-content band "
                        "yields no cohort, fall back to >0.95 near-clone comparators, "
                        "collapsed to one vote per clone group. OFF by default so a "
                        "default run reproduces the frozen M10 result")
    p.add_argument("--blastp",
                   help="blastp executable. Default: $RBPFINDER_BLASTP, else PATH. "
                        "A path given here must exist -- it is never silently replaced "
                        "by a PATH lookup")
    p.add_argument("--makeblastdb",
                   help="makeblastdb executable (needed only by C5). Default: "
                        "$RBPFINDER_MAKEBLASTDB, else PATH")
    p.add_argument("--host",
                   help="the bacterial host of this phage, e.g. 'Stenotrophomonas "
                        "maltophilia'. A configured DB_HOST is used only when it covers "
                        "this organism; without it the host database is skipped and the "
                        "reason recorded")
    p.add_argument("--stage2-backend", choices=["local", "ebi"], default="local",
                   help="where Stage 2 sequence evidence comes from. `local` searches "
                        "configured BLAST databases (default). `ebi` submits candidate "
                        "sequences to EBI, which requires remote_allowed plus per-run "
                        "authorisation. An explicit choice is never silently swapped")
    p.add_argument("--ebi-endpoint",
                   default="https://www.ebi.ac.uk/Tools/services/rest/ncbiblast",
                   help="EBI Job Dispatcher REST endpoint")
    p.add_argument("--ebi-email", default=os.environ.get("RBPFINDER_EBI_EMAIL"),
                   metavar="EBI_CONTACT_EMAIL",
                   help="EBI contact email. Used ONLY as contact information for the "
                        "EMBL-EBI Job Dispatcher. It is not an RBPfinder account, and "
                        "results are NOT sent to it -- RBPfinder polls, downloads and "
                        "parses them automatically, so you never fetch or hand back a "
                        "result file. Defaults to RBPFINDER_EBI_EMAIL. RBPfinder never "
                        "invents an address: the service uses it to reach the submitter "
                        "about their own jobs")
    p.add_argument("--host-taxid",
                   help="NCBI taxonomy id for --host. Required for the remote host "
                        "search: RBPfinder never guesses a taxonomy id")
    p.add_argument("--no-local-stage2", action="store_true",
                   help="do not search configured databases (fixtures, quick smokes, "
                        "reproduction runs)")
    p.add_argument("--stage2-manifest",
                   help="TSV indexing local Stage 2 results (cds_id, kind, path)")
    p.add_argument("--data-policy", choices=["private", "remote_allowed", "import_only"],
                   help="may the sequences leave this machine? decided once per run "
                        "(default comes from data_policy.default in the YAML)")
    p.add_argument("--allow-remote", action="store_true",
                   help="authorise third-party submission for THIS run only; required "
                        "before remote_allowed actually permits anything, and the only "
                        "way past the remote-submission confirmation prompt. The "
                        "disclosure is still printed and still recorded")
    p.add_argument("--restart-stage2", action="store_true",
                   help="search again from scratch instead of resuming. Resume is the "
                        "DEFAULT when a saved search state exists. This archives the "
                        "old state beside the new one and deletes nothing")
    p.add_argument("--quiet-progress", action="store_true",
                   help="do not print per-search remote progress. The event log is "
                        "written either way")
    p.add_argument("--suppress-nomination", action="append", metavar="CDS_ID",
                   help="TEST ONLY: drop this CDS from Stage 1 so Stage 6 recovery can "
                        "be demonstrated. Repeatable.")
    # M21-a/b. One run-wide switch, plus a per-provider override, generated from the
    # contract so the flags cannot drift from the providers that exist.
    p.add_argument("--evidence-mode", choices=["manual", "auto", "skip"], default=None,
                   help="how external evidence is acquired for the whole run: write "
                        "batches for you to run yourself (manual), submit and poll "
                        "(auto), or do not run it at all (skip). Each provider keeps "
                        "its own default when this is not given, and a provider that "
                        "cannot be automated stays manual and says so")
    for _name in sorted(_acq_provider_names()):
        p.add_argument("--%s-mode" % _name.replace("_", "-"),
                       dest="mode_" + _name, choices=["manual", "auto", "skip"],
                       default=None,
                       help="acquisition mode for %s only; overrides --evidence-mode. "
                            "Asking for a mode this provider does not support is an "
                            "error rather than a silent downgrade" % _name)
    args = p.parse_args(argv_list)
    # Exactly two expected error domains reach the user as a clean message. Everything
    # else keeps its traceback and exit 1 -- a broad `except Exception` here would dress
    # real defects up as environment problems and make the next clean test lie.
    # The exit code comes from the exception TYPE, never from parsing its text; that is
    # the same lesson as judging a download from disk state instead of stdout.
    try:
        return run(args)
    except CapabilityUnavailable as exc:
        sys.stderr.write("Capability unavailable: %s\n" % exc)
        sys.stderr.write("  This is a property of this installation, not a defect. "
                         "Install the tool, set the environment variable, or pass the "
                         "path explicitly.\n")
        return exc.exit_code
    except InputValidationError as exc:
        sys.stderr.write("Input error: %s\n" % exc)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
