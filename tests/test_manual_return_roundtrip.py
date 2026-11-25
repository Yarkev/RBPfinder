"""The manual arm: a returned structure result must reach the evidence chain.

Stage 3 runs Foldseek locally and automatically. The OTHER structural route is manual by
design -- AlphaFold needs a GPU or the AF Server, so the run writes
`AF3_MONOMER_BATCH.tsv` with per-candidate priorities, a person submits it, the predicted
structures go to Foldseek, and the result comes back through `--stage2-manifest`.

That path had **no test coverage at all** until this file, which is a bad place for a
blind spot: `stage3.af3_priority.P1_mandatory` calls structure "the arbiter", so a silent
break here would mean a mandatory arbitration that cannot be returned even when someone
does the work.

The round trip is verified against the dark-matter case, which is where family T matters
most -- a CDS with no annotation (no D) and no homolog hit (no S) caps at R3-E1 on G
alone, and only structure can move it.
"""
import hashlib
import json

import pytest

from rbpfinder import cli
import test_end_to_end as e2e


def _returned_foldseek(path, translation, functional=True):
    """A Foldseek-web-shaped result, the same shape local Stage 3 writes.

    Local Stage 3 records its own artefacts with `kind: foldseek_json` too, so a hand
    returned file and an automatic one enter through the identical path -- which is why
    this test covers both arms of the contract.
    """
    title = ("7xyz_A Crystal structure of a phage tailspike receptor binding domain"
             if functional else "7xyz_A Crystal structure of a bacteriophage tail tube")
    path.write_text(json.dumps({"results": [{"db": "pdb100", "alignments": [[{
        "target": title, "prob": 0.98, "eval": 1e-30, "score": 520.0, "seqId": 0.41,
        "qStartPos": 5, "qEndPos": max(len(translation) - 5, 10),
        "dbStartPos": 1, "dbEndPos": max(len(translation) - 10, 10),
        "qLen": len(translation), "taxName": "Salmonella phage"}]]}]}),
        encoding="utf-8")
    return path


def _run(tmp_path, name, extra=()):
    gbk = tmp_path / "syn.gbk"
    if not gbk.exists():
        e2e._genbank(gbk)
    out = tmp_path / name
    assert cli.main(["--genbank", str(gbk), "--phage-id", "RT", "--out", str(out),
                     "--no-local-stage2", "--no-local-stage3", "--quiet-progress",
                     *extra]) == 0
    return json.loads((out / "run_result.json").read_text(encoding="utf-8"))


def _candidate(result, cds_id):
    return next((c for c in result["candidates"] if c["cds_id"] == cds_id), None)


def _dark_matter_candidate(result):
    """A pool member with no annotation: no D record, and with Stage 2 off, no S."""
    for c in result["candidates"]:
        if (c["annotation"]["product"] or "").lower().startswith("hypothetical"):
            return c
    return None


def test_a_returned_structure_result_reaches_the_evidence_chain(tmp_path):
    before = _run(tmp_path, "before")
    dark = _dark_matter_candidate(before)
    assert dark is not None, "fixture no longer has an unannotated CDS in the pool"
    assert dark["independent_families"] == ["G"], dark["independent_families"]
    assert (dark["tier_r"], dark["tier_e"]) == ("R3", "E1")

    # the run asked for exactly this: structure as the arbiter
    assert dark.get("af3_priority") == "P1"

    gbk = tmp_path / "syn.gbk"
    from rbpfinder.ingest import genbank as genbank_in
    rows, _meta = genbank_in.read(gbk, phage_id="RT")
    translation = next(r["translation"] for r in rows if r["cds_id"] == dark["cds_id"])

    art = _returned_foldseek(tmp_path / "returned.foldseek.json", translation)
    manifest = tmp_path / "manifest.tsv"
    manifest.write_text(
        "cds_id\tkind\tpath\tsequence_sha256\tbatch_id\n"
        "%s\tfoldseek_json\t%s\t%s\t\n"
        % (dark["cds_id"], art, hashlib.sha256(translation.encode()).hexdigest()),
        encoding="utf-8")

    after = _run(tmp_path, "after", ["--stage2-manifest", str(manifest)])
    lifted = _candidate(after, dark["cds_id"])
    assert sorted(lifted["independent_families"]) == ["G", "T"]
    assert (lifted["tier_r"], lifted["tier_e"]) == ("R2", "E2")


def test_the_returned_result_is_what_moved_it(tmp_path):
    """Guard against the tier moving for some unrelated reason: every OTHER candidate
    must be untouched by one candidate's returned structure."""
    before = _run(tmp_path, "b2")
    dark = _dark_matter_candidate(before)
    gbk = tmp_path / "syn.gbk"
    from rbpfinder.ingest import genbank as genbank_in
    rows, _meta = genbank_in.read(gbk, phage_id="RT")
    translation = next(r["translation"] for r in rows if r["cds_id"] == dark["cds_id"])

    art = _returned_foldseek(tmp_path / "r2.foldseek.json", translation)
    manifest = tmp_path / "m2.tsv"
    manifest.write_text(
        "cds_id\tkind\tpath\tsequence_sha256\tbatch_id\n"
        "%s\tfoldseek_json\t%s\t%s\t\n"
        % (dark["cds_id"], art, hashlib.sha256(translation.encode()).hexdigest()),
        encoding="utf-8")
    after = _run(tmp_path, "a2", ["--stage2-manifest", str(manifest)])

    for c in before["candidates"]:
        if c["cds_id"] == dark["cds_id"]:
            continue
        other = _candidate(after, c["cds_id"])
        assert (other["tier_r"], other["tier_e"]) == (c["tier_r"], c["tier_e"]), c["cds_id"]
        assert other["independent_families"] == c["independent_families"], c["cds_id"]


def test_the_batch_file_carries_what_a_submitter_needs(tmp_path):
    """The handoff is only real if the file is usable on its own: who, why, how urgent."""
    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    out = tmp_path / "batch"
    assert cli.main(["--genbank", str(gbk), "--phage-id", "RT", "--out", str(out),
                     "--no-local-stage2", "--no-local-stage3", "--quiet-progress"]) == 0
    header = (out / "AF3_MONOMER_BATCH.tsv").read_text(
        encoding="utf-8").splitlines()[0].split("\t")
    for col in ("candidate_id", "priority", "trigger", "requested_reason"):
        assert col in header, header


def test_a_mismatched_sequence_is_refused_not_misattributed(tmp_path):
    """A returned file must belong to the sequence it was requested for. Attaching one
    protein's structure to another is a confident wrong answer nothing downstream could
    detect, so the sha256 binding is the thing standing between the manual arm and that.
    """
    before = _run(tmp_path, "b3")
    dark = _dark_matter_candidate(before)
    art = _returned_foldseek(tmp_path / "r3.foldseek.json", "M" + "A" * 199)
    manifest = tmp_path / "m3.tsv"
    manifest.write_text(
        "cds_id\tkind\tpath\tsequence_sha256\tbatch_id\n"
        "%s\tfoldseek_json\t%s\t%s\t\n"
        % (dark["cds_id"], art, hashlib.sha256(b"a different protein").hexdigest()),
        encoding="utf-8")
    after = _run(tmp_path, "a3", ["--stage2-manifest", str(manifest)])
    lifted = _candidate(after, dark["cds_id"])
    # refused: the family must NOT appear on a binding mismatch
    assert "T" not in lifted["independent_families"], lifted["independent_families"]


# ------------------------------------------- the handoff must be actionable and honest

def test_the_batch_carries_the_sequence_itself(tmp_path):
    """A batch naming the proteins and the reason each is wanted, but not WHAT TO
    SUBMIT, cannot be acted on from the file alone -- and the structure step is the one
    the rules call mandatory for six triggers. HHPRED_BATCH had carried the sequence all
    along; this one did not until 2026-11-25."""
    import csv
    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    out = tmp_path / "seqcol"
    assert cli.main(["--genbank", str(gbk), "--phage-id", "RT", "--out", str(out),
                     "--no-local-stage2", "--no-local-stage3", "--quiet-progress"]) == 0
    rows = list(csv.DictReader(
        (out / "AF3_MONOMER_BATCH.tsv").open(encoding="utf-8"), delimiter="\t"))
    assert rows, "no candidates requested, so this proves nothing"
    assert all(r["sequence"] for r in rows)
    # the checksum stays beside it: that is what binds a returned file to its request
    assert all(r["sequence_sha256"] for r in rows)


def test_the_rules_do_not_require_a_hosted_predictor(rules):
    """`return_via: foldseek` means what returns is a structure searched with Foldseek,
    so anything producing a PDB/mmCIF satisfies the step. Hardcoding a service name
    obscured that, and a hosted predictor would break this build's central promise."""
    af = rules.get("evidence_acquisition.providers.alphafold")
    assert "default" in af["service_name"], af["service_name"]
    assert af["return_via"] == "foldseek"
    local = af["local_predictors_satisfy_this"]
    assert {"esmfold", "colabfold_or_af2", "boltz_or_chai", "af3"} <= set(local)


def test_the_transmission_cost_is_stated_where_the_handoff_happens():
    """The user chose this build because sequences never leave the machine. An agent
    handing over the batch must say that a hosted predictor breaks that, before they act
    rather than after."""
    import pathlib as _p
    root = _p.Path(__file__).resolve().parent.parent
    skill = (root / "packaging" / "biomni" / "skill" / "rbpfinder"
             / "SKILL.md").read_text(encoding="utf-8")
    assert "breaks the promise" in skill
    assert "local predictor satisfies the step" in skill
    yaml_text = (root / "rules" / "decision_rules.yaml").read_text(encoding="utf-8")
    assert "TRANSMISSION WARNING" in yaml_text
