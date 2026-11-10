"""RBPfinder as Biomni custom tools -- the standardised server-side route.

One genome in, a defensible RBP shortlist out, with the division of labour fixed:

    Biomni  annotates (pharokka; plus phold only on the accurate route)
    RBPfinder  classifies -- and is the ONLY thing that decides what is an RBP

RBPfinder deliberately does not annotate genomes. Keeping the annotator outside it means
a pharokka or PHROGs upgrade cannot quietly move a tier, and the judgment layer stays
frozen, auditable and reproducible. Biomni is the orchestrator that produces the
annotation; these tools are the contract between the two.

Three tools, called in this order:

    1. rbpfinder_workflow_plan()   decide fast vs accurate -- ASK the user first
    2. check_annotation()          is the annotation usable? seconds, before any analysis
    3. run_rbpfinder()             the analysis

Biomni's A1 agent registers a documented callable with ``agent.add_tool(fn)`` and builds
the schema from the source, so each docstring below is the contract the agent reads.
``register(agent)`` wires all three in, plus the software and database declarations.

These tools are OPERATORS of the installed ``rbpfinder`` CLI, never a second
implementation. They run it and report what it produced. They must not re-derive tiers,
Primary/Rescue membership, direction, or completeness -- every such conclusion comes from
the run. This mirrors the Agent Skill in ``packaging/skill/rbpfinder``; keep them in step.
"""
import json
import os
import pathlib
import subprocess

FAST = "fast"
ACCURATE = "accurate"


def rbpfinder_workflow_plan(mode=None):
    """Plan an RBP search: pick the route, and get the exact annotation contract.

    CALL THIS FIRST, before annotating anything. RBPfinder needs an annotated genome, and
    the two routes differ by roughly an hour of CPU, so the choice belongs to the user.

    If `mode` is None this returns a question to PUT TO THE USER and nothing is run. Ask
    it, then call this again with their answer. If the user has already said they want it
    quick, or says they do not care, use "fast" -- that is the default and it still
    produces structural evidence.

    Args:
        mode: None to get the question to ask. "fast" (default route) runs pharokka only;
            "accurate" runs pharokka and then phold.

    Returns:
        With mode=None: {next_action: "ask_user", question, options} -- ask, do not guess.
        With a mode: {mode, steps, annotation_commands, deliverable, validate_with,
        then_call, compute} -- the standardised pipeline for that route.

    The difference between the routes, stated honestly:

      fast (default)   pharokka annotation, then RBPfinder with local Foldseek structure
                       search over the nominated candidate pool. Structural (family T)
                       evidence IS obtained -- it is searched for the candidates rather
                       than precomputed for every CDS. Minutes.

      accurate         pharokka, then phold over the whole genome, then RBPfinder on
                       phold's GenBank with the same local Foldseek step. What phold buys
                       is better annotation: it resolves CDS that pharokka leaves as
                       "hypothetical protein", which improves nomination and the
                       direction read off a product name. It is 1-2 hours on CPU.
                       It does NOT add an independent structural evidence family --
                       RBPfinder's de-correlation collapses a phold hit and a Foldseek hit
                       to one family, by design.

    Use accurate when the genome is poorly annotated (many hypothetical proteins), when a
    fast run came back `completeness: limited`, or when the user wants the most thorough
    answer and can wait. Otherwise fast.
    """
    if mode is None:
        return {
            "next_action": "ask_user",
            "question": (
                "RBP search for this genome -- which route? "
                "(1) Fast, the default: pharokka annotation plus local structure search "
                "over the candidate proteins. A few minutes, and structural evidence is "
                "still used. "
                "(2) Accurate: additionally runs phold over the whole genome first, which "
                "resolves proteins pharokka leaves as 'hypothetical' and so improves the "
                "shortlist on poorly annotated genomes. Costs 1-2 hours of CPU. "
                "If you have no preference, the fast route is used."),
            "options": {
                FAST: "pharokka + RBPfinder (local structure search on candidates); minutes",
                ACCURATE: "pharokka + phold + RBPfinder; 1-2 h extra CPU, better on "
                          "poorly annotated genomes",
            },
            "default_if_no_preference": FAST,
        }

    mode = str(mode).strip().lower()
    if mode not in (FAST, ACCURATE):
        return {"status": "error",
                "problem": "mode must be %r or %r (got %r)" % (FAST, ACCURATE, mode)}

    # The commands are the expected SHAPE. Verify the flags against the installed version
    # (`pharokka.py --help`, `phold run --help`) -- they move between releases. What is
    # fixed, and what RBPfinder actually depends on, is the DELIVERABLE below.
    ann = ["pharokka.py -i <genome.fasta> -o <pharokka_out> -d <PHROGS_DB> -t <threads> -f"]
    if mode == ACCURATE:
        ann.append("phold run -i <pharokka_out>/pharokka.gbk -o <phold_out> -t <threads>")

    deliverable = ("<pharokka_out>/pharokka.gbk" if mode == FAST
                   else "<phold_out>/phold.gbk")
    return {
        "mode": mode,
        "steps": [
            "1. annotate with the command(s) in annotation_commands (this is Biomni's "
            "job; RBPfinder does not annotate)",
            "2. call check_annotation() on the deliverable -- it takes seconds and fails "
            "with a precise reason, so do it before the analysis",
            "3. call run_rbpfinder() with that file and this mode",
            "4. report Primary, then Rescue, then the completeness state and its reasons, "
            "using those names and no others",
        ],
        "annotation_commands": ann,
        "deliverable": deliverable,
        "deliverable_requirements": (
            "an annotated GenBank whose CDS features carry /translation and whose locus "
            "tags end in CDS_<number> (pharokka's own naming). Protein sequence is "
            "required: without it neither sequence homology nor local structure search "
            "can run."),
        "do_not_hand_over": (
            "phold's per-CDS predictions TSV instead of its GenBank. The table carries no "
            "protein sequence, so local structure search would be silently unavailable. "
            "Use phold.gbk, which has phold's improved products AND the translations."),
        "validate_with": "check_annotation(genbank_path=<deliverable>)",
        "then_call": "run_rbpfinder(genbank_path=<deliverable>, mode=%r, ...)" % mode,
        "compute": ("pharokka ~15 min; RBPfinder minutes" if mode == FAST else
                    "pharokka ~15 min; phold 1-2 h on 8 CPU cores; RBPfinder minutes"),
        "note": ("Structural evidence comes from local Foldseek either way -- it runs on "
                 "this machine and transmits nothing. phold is about annotation quality, "
                 "not about adding a second structural evidence family."),
    }


def check_annotation(genbank_path=None, phold_tsv=None):
    """Check that an annotation can actually be analysed, before spending time on it.

    Runs `rbpfinder validate-input`, which uses the same readers the analysis uses, so a
    file this accepts is a file the analysis accepts. Takes seconds. Call it after
    annotating and before run_rbpfinder -- and, on the accurate route, you can also call
    it on the pharokka GenBank before committing an hour to phold.

    Args:
        genbank_path: an annotated GenBank (pharokka.gbk or phold.gbk).
        phold_tsv: a phold per-CDS predictions table, if that is all you have. It is
            accepted but carries no protein sequence, so local structure search cannot
            run from it; prefer phold's GenBank.

    Returns:
        {usable: bool, cds_count, with_translation, supports_local_structure_search,
         problems: [...], warnings: [...]}. When usable is False, `problems` says exactly
        what to fix -- report that to the user rather than proceeding.
    """
    if not genbank_path and not phold_tsv:
        return {"usable": False,
                "problems": ["give either genbank_path or phold_tsv"]}
    cmd = ["rbpfinder", "validate-input", "--json"]
    cmd += (["--genbank", str(genbank_path)] if genbank_path
            else ["--phold", str(phold_tsv)])
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"usable": False,
                "problems": ["could not run rbpfinder validate-input: %s"
                             % ((proc.stderr or proc.stdout) or "no output")[-500:]]}


def run_rbpfinder(
    genbank_path,
    phage_id,
    out_dir,
    mode=FAST,
    host=None,
    foldseek_db=None,
    prostt5_model=None,
    allow_remote=False,
    ebi_email=None,
):
    """Prioritise receptor-binding protein (RBP) candidates in one tailed dsDNA phage.

    Runs the installed RBPfinder on an annotated GenBank and returns its shortlist.
    RBPfinder is a rule-based classifier for Caudoviricetes (tailed dsDNA phages) only; it
    prioritises experimental candidates, it does not validate biology.

    Call rbpfinder_workflow_plan() first to settle the route with the user, and
    check_annotation() on the file before calling this.

    Local Foldseek structure search (family T evidence) runs on this machine when it is
    configured -- it is what lifts a true RBP to the top tier and separates a real binder
    from an apparatus protein that merely sits in the tail. No sequence is uploaded for it.

    Args:
        genbank_path: the annotated GenBank from the chosen route -- pharokka.gbk (fast)
            or phold.gbk (accurate). CDS must carry /translation. A bare genome FASTA is
            refused: annotate it first.
        phage_id: short identifier for this phage; prefixes every CDS id.
        out_dir: directory to write the run into. Never reuse an input path.
        mode: "fast" or "accurate" -- recorded in the result so the route is traceable.
            It does not change the decision rules; it describes which annotation was fed in.
        host: bacterial host as "Genus species", e.g. "Acinetobacter baumannii". A
            configured host BLAST database is used only when it covers this organism.
            Optional, but it materially improves accuracy when a matching database exists.
        foldseek_db: local structure database as "label=path", e.g.
            "pdb100=/workspace/foldseek/pdb100". Defaults to $RBPFINDER_FOLDSEEK_DB.
        prostt5_model: directory of ProstT5 weights. Defaults to $RBPFINDER_PROSTT5_MODEL.
        allow_remote: if True, permit remote EBI BLAST, which transmits candidate protein
            sequences off this machine. Requires ebi_email. Default False: nothing is sent.
            Only set it after telling the user that sequences will be transmitted.
        ebi_email: EBI Job Dispatcher contact email, required only with allow_remote. It
            is EBI contact information, NOT an RBPfinder account and NOT where results
            are delivered -- RBPfinder polls and downloads automatically.

    Returns:
        {status, mode, primary, rescue, completeness: {state, assessment_coverage,
         reasons}, output_dir, run_result_json, report_md, screening_table, note}

        primary -- the shortlist to test first.
        rescue  -- kept only to protect recall; the evidence is NOT strong.
        completeness -- "complete" / "partial" / "limited" with the reasons as produced.

    Report Primary, then Rescue, then completeness, using those names. Do NOT expand
    Rescue, re-rank the output, or present a `partial`/`limited` run as biologically
    complete -- the completeness state is the user's only signal of how far to trust it.
    """
    env = dict(os.environ)
    fdb = foldseek_db or env.get("RBPFINDER_FOLDSEEK_DB")
    p5 = prostt5_model or env.get("RBPFINDER_PROSTT5_MODEL")

    pre = check_annotation(genbank_path=genbank_path)
    if not pre.get("usable"):
        return {"status": "error", "stage": "annotation_check", "mode": mode,
                "problems": pre.get("problems"),
                "hint": "fix the annotation and re-validate; nothing was analysed"}

    cmd = ["rbpfinder", "--genbank", str(genbank_path), "--phage-id", str(phage_id),
           "--out", str(out_dir)]
    if host:
        cmd += ["--host", host]
    if fdb:
        for pair in str(fdb).replace(";", ",").split(","):
            pair = pair.strip()
            if pair:
                cmd += ["--foldseek-db", pair]
    if p5:
        cmd += ["--prostt5-model", str(p5)]
    if allow_remote:
        cmd += ["--stage2-backend", "ebi", "--data-policy", "remote_allowed",
                "--allow-remote"]
        if ebi_email:
            cmd += ["--ebi-email", ebi_email]

    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    rr = pathlib.Path(out_dir) / "run_result.json"
    if proc.returncode != 0 or not rr.exists():
        return {"status": "error", "stage": "analysis", "mode": mode,
                "exit_code": proc.returncode,
                "stderr": (proc.stderr or "")[-2000:],
                "hint": {2: "input error -- is the GenBank annotated with /translation?",
                         3: "a required capability is missing; run `rbpfinder doctor`",
                         1: "unexpected internal error"}.get(proc.returncode, "")}

    result = json.loads(rr.read_text(encoding="utf-8"))
    by_id = {x.get("cds_id"): x for x in result.get("candidates", []) or []}

    def shortlist(key):
        out = []
        for c in result.get(key, []) or []:
            cid = c.get("cds_id") if isinstance(c, dict) else c
            rec = by_id.get(cid, {})
            ann = rec.get("annotation")
            product = ann.get("product") if isinstance(ann, dict) else ann
            out.append({"cds_id": cid,
                        "tier": "%s-%s" % (rec.get("tier_r"), rec.get("tier_e")),
                        "role": rec.get("protein_role"),
                        "annotation": product,
                        "independent_families": rec.get("independent_families"),
                        "rbp_evidence_localisation": rec.get(
                            "rbp_evidence_localisation")})
        return out

    reasons = [lim.get("text") for lim in result.get("limitations", []) or []
               if lim.get("text")]
    if result.get("assessment_coverage") == "reduced":
        reasons.append("assessment coverage is reduced on this installation (a missing "
                       "genome-wide capability caps completeness; it is a property of "
                       "the install, not of the phage)")

    s3 = result.get("stage3_local") or {}
    return {
        "status": "ok",
        "mode": mode,
        "primary": shortlist("primary_set"),
        "rescue": shortlist("rescue_set"),
        "completeness": {"state": result.get("completeness_assessment"),
                         "assessment_coverage": result.get("assessment_coverage"),
                         "reasons": reasons},
        "local_structure_search": {"ran": s3.get("mode") == "local_foldseek"
                                   and bool(s3.get("cds_searched")),
                                   "scope": s3.get("scope"),
                                   "cds_searched": s3.get("cds_searched"),
                                   "cds_with_hits": s3.get("cds_with_hits"),
                                   "reason": s3.get("reason")},
        "output_dir": str(out_dir),
        "run_result_json": str(rr),
        "report_md": str(pathlib.Path(out_dir) / "RBP_report.md"),
        "screening_table": str(pathlib.Path(out_dir) / "all_CDS_screening.tsv"),
        "note": (
            "Primary is the shortlist; Rescue protects recall and its evidence is not "
            "strong; the completeness state is honest and must not be smoothed into "
            "biological completeness. searched-no-hit is not not-run; absence of evidence "
            "never lowers a tier; one sequence-homology family stays one family however "
            "many databases were searched. Do not expand Rescue or re-rank. "
            "all_CDS_screening.tsv answers 'why was this CDS not a candidate?'."),
    }


def register(agent):
    """Wire RBPfinder into a Biomni A1 agent: the three tools, the software, the data.

    Call once after constructing the agent:

        from rbpfinder_biomni import register
        register(agent)
    """
    agent.add_tool(rbpfinder_workflow_plan)
    agent.add_tool(check_annotation)
    agent.add_tool(run_rbpfinder)
    agent.add_software({
        "rbpfinder": "Rule-based prioritisation of receptor-binding protein candidates "
                     "in tailed dsDNA (Caudoviricetes) phage genomes, from an annotated "
                     "GenBank. Does NOT annotate genomes -- pharokka/phold do that. "
                     "CLI: rbpfinder, rbpfinder doctor, rbpfinder validate-input.",
        "foldseek": "Structure search; RBPfinder uses it locally (ProstT5) for family-T "
                    "evidence with nothing transmitted.",
    })
    data = {}
    fdb = os.environ.get("RBPFINDER_FOLDSEEK_DB", "")
    for pair in fdb.replace(";", ",").split(","):
        if "=" in pair:
            label, path = pair.split("=", 1)
            data[path.strip()] = ("Foldseek structure database (%s) for local RBPfinder "
                                  "Stage 3" % label.strip())
    if os.environ.get("RBPFINDER_PROSTT5_MODEL"):
        data[os.environ["RBPFINDER_PROSTT5_MODEL"]] = (
            "ProstT5 weights: sequence-to-3Di for local Foldseek structure search")
    if data:
        agent.add_data(data)
    return agent
