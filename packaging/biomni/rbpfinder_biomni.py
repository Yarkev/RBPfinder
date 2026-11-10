"""RBPfinder as a Biomni custom tool.

Biomni's A1 agent registers a plain, well-documented callable with
``agent.add_tool(fn)``; it introspects the source and docstring to build the tool
schema, so the docstring below is the contract the agent reads. ``register(agent)`` wires
the tool in and declares the software and databases this install provides.

The tool is an OPERATOR of the installed ``rbpfinder`` CLI, never a second implementation.
It runs one analysis and reports what the CLI produced. It must not re-derive tiers,
Primary/Rescue membership, direction, or completeness -- every such conclusion comes from
the run, not from the agent narrating one. This mirrors the Agent Skill in
``packaging/skill/rbpfinder``; keep the two in step.
"""
import json
import os
import pathlib
import subprocess


def run_rbpfinder(
    genbank_path,
    phage_id,
    out_dir,
    host=None,
    foldseek_db=None,
    prostt5_model=None,
    allow_remote=False,
    ebi_email=None,
):
    """Prioritise receptor-binding protein (RBP) candidates in one tailed dsDNA phage.

    Runs the installed RBPfinder on an annotated (pharokka-style) GenBank file and returns
    its shortlist. RBPfinder is a rule-based classifier for Caudoviricetes (tailed dsDNA
    phages) only; it does not validate biology, it prioritises experimental candidates.

    On this Linux host it can run local Foldseek structure search (family T evidence),
    which is what lifts a true RBP to the top tier and separates a real binder from an
    apparatus protein that merely sits in the tail. Structure runs on the machine: no
    sequence is uploaded for it.

    Args:
        genbank_path: Path to an annotated GenBank file with CDS /translation features
            (pharokka output). A bare nucleotide genome will be rejected -- annotate it
            first.
        phage_id: Short identifier for this phage, used to prefix every CDS id.
        out_dir: Directory to write the run into (created if absent). Never reuse an
            input path.
        host: Bacterial host as "Genus species", e.g. "Klebsiella pneumoniae". A
            configured host BLAST database is used only when it covers this organism.
            Optional but materially improves accuracy when a matching host DB exists.
        foldseek_db: Local structure database as "label=path", e.g.
            "pdb100=/workspace/foldseek/pdb100". Defaults to $RBPFINDER_FOLDSEEK_DB.
        prostt5_model: Directory of ProstT5 weights so an amino-acid query is searched
            directly. Defaults to $RBPFINDER_PROSTT5_MODEL.
        allow_remote: If True, permit remote EBI BLAST for sequences that may leave the
            machine. Requires ebi_email. Default False (nothing is transmitted).
        ebi_email: EBI Job Dispatcher contact email, required only when allow_remote is
            True. It is EBI contact information, NOT an RBPfinder account and NOT where
            results are delivered -- RBPfinder polls and downloads automatically.

    Returns:
        A dict with:
          status: "ok" or "error"
          primary: the shortlist to test first (list of {cds_id, tier, role, annotation})
          rescue: candidates kept only to protect recall, evidence not strong
          completeness: "complete" | "partial" | "limited" and the stated reason -- read
            this; a limited result is honest, not a failure, and must not be presented as
            biological completeness
          output_dir, run_result_json, report_md: paths to the full artefacts
          note: scientific invariants to preserve when summarising

    The caller must NOT expand Rescue, re-rank, or claim completeness the run did not
    report. Report Primary, then Rescue, then the completeness state and its reason.
    """
    env = dict(os.environ)
    fdb = foldseek_db or env.get("RBPFINDER_FOLDSEEK_DB")
    p5 = prostt5_model or env.get("RBPFINDER_PROSTT5_MODEL")

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
        return {"status": "error", "exit_code": proc.returncode,
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
                        "annotation": product})
        return out

    # completeness_assessment is the reported state (a bare string, e.g. "partial"); the
    # reasons are the recorded limitations and the coverage flag. Both are reported as
    # produced -- the tool states them, it does not compute or soften them.
    reasons = [lim.get("text") for lim in result.get("limitations", []) or []
               if lim.get("text")]
    if result.get("assessment_coverage") == "reduced":
        reasons.append("assessment coverage is reduced on this installation (a missing "
                       "genome-wide capability caps completeness; it is a property of the "
                       "install, not of the phage)")
    return {
        "status": "ok",
        "primary": shortlist("primary_set"),
        "rescue": shortlist("rescue_set"),
        "completeness": {"state": result.get("completeness_assessment"),
                         "assessment_coverage": result.get("assessment_coverage"),
                         "reasons": reasons},
        "output_dir": str(out_dir),
        "run_result_json": str(rr),
        "report_md": str(pathlib.Path(out_dir) / "RBP_report.md"),
        "note": (
            "Primary is the shortlist; Rescue protects recall and its evidence is not "
            "strong; the completeness state is honest and must not be smoothed into "
            "biological completeness. searched-no-hit is not not-run; absence of evidence "
            "never lowers a tier; one sequence-homology family stays one family however "
            "many databases were searched. Do not expand Rescue or re-rank."),
    }


def register(agent):
    """Wire RBPfinder into a Biomni A1 agent: the tool, the software, the databases.

    Call once after constructing the agent:

        from rbpfinder_biomni import register
        register(agent)
    """
    agent.add_tool(run_rbpfinder)
    agent.add_software({
        "rbpfinder": "Rule-based prioritisation of receptor-binding protein candidates "
                     "in tailed dsDNA (Caudoviricetes) phage genomes, from an annotated "
                     "GenBank file. CLI: rbpfinder.",
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
