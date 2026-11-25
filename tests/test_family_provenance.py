"""Which backend earned each family, and which query produced family T.

`independent_families` says a family counted. It does not say what produced it -- and
with more than one route to the same family, that is the difference between a number you
can compare with another run and one you cannot.

Family T has two routes that are **not** interchangeable:

  * ProstT5 -- a sequence becomes 3Di and is searched. No backbone coordinates, so
    Foldseek cannot compute a probability at all: `probability_pct` is null for every
    hit, and that is the expected state rather than a failed search.
  * a predicted or experimental structure searched directly, where probabilities are
    real numbers.

Before 2026-11-25 the output exposed neither, and both routes recorded the same tool id.
A consumer seeing a null `probability_pct` could not tell which case it had, so mixing
the routes -- which is exactly what adding a structure predictor does -- would have made
any assumption about that field quietly wrong on some rows and not others.
"""
import hashlib
import json

import pathlib

import pytest

from rbpfinder import cli
from rbpfinder.evidence import structural
from rbpfinder.ingest import genbank as genbank_in
import test_end_to_end as e2e

ROOT = pathlib.Path(__file__).resolve().parent.parent

FUNCTIONAL = "7xyz_A Crystal structure of a phage tailspike receptor binding domain"


def _hit(prob=None):
    a = {"target": FUNCTIONAL, "eval": 1e-30, "score": 520.0, "seqId": 0.41,
         "qStartPos": 5, "qEndPos": 195, "dbStartPos": 1, "dbEndPos": 190,
         "qLen": 200, "taxName": "Salmonella phage"}
    if prob is not None:
        a["prob"] = prob
    return a


def _run_with_return(tmp_path, name, prob, declare=None):
    gbk = tmp_path / "syn.gbk"
    if not gbk.exists():
        e2e._genbank(gbk)
    rows, _meta = genbank_in.read(gbk, phage_id="PV")
    dark = next(r for r in rows
                if (r["product"] or "").lower().startswith("hypothetical"))
    art = tmp_path / ("%s.foldseek.json" % name)
    doc = {"results": [{"db": "pdb100", "alignments": [[_hit(prob)]]}]}
    if declare:
        doc["query_kind"] = declare
    art.write_text(json.dumps(doc), encoding="utf-8")
    man = tmp_path / ("%s.tsv" % name)
    man.write_text(
        "cds_id\tkind\tpath\tsequence_sha256\tbatch_id\n%s\tfoldseek_json\t%s\t%s\t\n"
        % (dark["cds_id"], art,
           hashlib.sha256(dark["translation"].encode()).hexdigest()), encoding="utf-8")
    out = tmp_path / name
    assert cli.main(["--genbank", str(gbk), "--phage-id", "PV", "--out", str(out),
                     "--no-local-stage2", "--no-local-stage3", "--quiet-progress",
                     "--stage2-manifest", str(man)]) == 0
    d = json.loads((out / "run_result.json").read_text(encoding="utf-8"))
    return next(c for c in d["candidates"] if c["cds_id"] == dark["cds_id"])


# ------------------------------------------------- the query kind, read off the data

def test_a_hit_carrying_a_probability_is_a_structure_query():
    assert structural.query_kind([{"probability": 0.98}]) == "backbone_structure"


def test_a_missing_probability_alone_is_not_enough_to_name_the_route():
    """The trap, found by a real structure-structure run. A probability can be absent
    for two unrelated reasons: the query had no backbone coordinates (ProstT5), or the
    search's output format simply did not request the column. An earlier version read
    the second as the first, which mis-attributes the route -- the exact failure this
    field exists to prevent."""
    assert structural.query_kind([{"probability": None}]) == "unknown"


def test_a_declaration_beats_inference():
    """Local Stage 3 ran ProstT5 and knows; an HPC route searched a structure and knows.
    Whoever knows says so, and inference is only the fallback."""
    assert structural.query_kind(
        [{"probability": None, "declared_query_kind": "sequence_3di"}]) == "sequence_3di"
    assert structural.query_kind(
        [{"probability": 0.9, "declared_query_kind": "sequence_3di"}]) == "sequence_3di"


def test_local_stage3_stamps_its_own_artefacts():
    """Otherwise the commonest route would report `unknown` forever, since ProstT5
    output is exactly the shape inference cannot resolve."""
    src = (ROOT / "rbpfinder" / "stage3_local.py").read_text(encoding="utf-8")
    assert 'doc["query_kind"] = "sequence_3di"' in src


def test_no_hits_is_neither_rather_than_guessed():
    """With nothing returned the route genuinely cannot be told from the data, and
    saying so beats picking one."""
    assert structural.query_kind([]) == "no_hits"


def test_it_is_derived_not_declared():
    """A manifest row can come from either route, so a claim in it would be
    unverifiable. The hits are the evidence."""
    src = structural.__doc__ or ""
    assert "query_kind" in dir(structural)
    assert structural.query_kind([{"probability": 0.5}, {"probability": None}]) == \
        "backbone_structure"


# ------------------------------------------------------- both routes, end to end

def test_the_two_routes_are_distinguishable_in_the_output(tmp_path):
    """The point of the whole exercise: same family, same tier, different provenance."""
    struct = _run_with_return(tmp_path, "struct", prob=0.98)
    prost = _run_with_return(tmp_path, "prost", prob=None, declare="sequence_3di")

    assert struct["structure_query"] == "backbone_structure"
    assert prost["structure_query"] == "sequence_3di"
    # identical conclusions -- which is why the output has to carry the difference
    assert struct["independent_families"] == prost["independent_families"] == ["G", "T"]
    assert (struct["tier_r"], struct["tier_e"]) == (prost["tier_r"], prost["tier_e"])


def test_family_provenance_names_the_backend_for_every_counted_family(tmp_path):
    c = _run_with_return(tmp_path, "prov", prob=0.98)
    assert set(c["family_provenance"]) == set(c["independent_families"])
    assert c["family_provenance"]["T"] == "foldseek_structure"
    assert c["family_provenance"]["G"] == "c1_module_position"


def test_provenance_covers_the_representatives_not_every_record(tmp_path):
    """De-correlation picks one record per family; provenance must name THAT one, or it
    would credit a family to a record that did not count."""
    c = _run_with_return(tmp_path, "reps", prob=0.98)
    assert len(c["family_provenance"]) == len(c["independent_families"])


def test_structure_query_is_absent_when_no_structural_family_counted(tmp_path):
    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    out = tmp_path / "noT"
    assert cli.main(["--genbank", str(gbk), "--phage-id", "PV", "--out", str(out),
                     "--no-local-stage2", "--no-local-stage3", "--quiet-progress"]) == 0
    d = json.loads((out / "run_result.json").read_text(encoding="utf-8"))
    for c in d["candidates"]:
        if "T" not in c["independent_families"]:
            assert c["structure_query"] is None


# ------------------------------------- the geometry record, which has no hits to read

def test_the_geometry_record_does_not_infer_from_hits(rules):
    """It comes FROM a predicted structure by definition. It also takes no `hits`
    argument -- an earlier edit inserted the inference there anyway, which would have
    been a NameError the moment anything called it. Nothing does, which is why the
    tests stayed green; dead code hides this class of mistake."""
    rec = {"cds_id": "X", "length_aa": 300}
    out = structural.geometry_record(rec, {"rg_ratio": 3.0, "mean_plddt": 80},
                                     rules, "af3_local")
    assert out["structure_query"] == "backbone_structure"
    # and pLDDT may never assert receptor binding
    assert out["direction"] in ("supports_structural_only", "uninformative")
    assert out["is_heuristic"] is True


# ---------------------------------------- the executor tiers, a maintainer decision

def test_the_three_tiers_are_declared_with_hpc_off_by_default(rules):
    """Added 2026-11-25. Until then the rules knew only "the tool never transmits",
    which left no way to describe a destination the user trusts but the tool does not
    run on -- so a platform that could submit to a cluster had no sanctioned route and
    no stated boundary either."""
    tiers = rules.get("evidence_acquisition.providers.alphafold.execution_tiers")
    assert tiers["public_web_service"]["allowed"] is False
    assert tiers["user_local"]["allowed"] is True
    hpc = tiers["session_trusted_hpc"]
    assert hpc["allowed"] is True
    assert hpc["default"] is False, "the offline promise must not change silently"
    assert "confirmation" in hpc["requires"]
    assert hpc["must_record"], "a trusted destination has to stay checkable afterwards"


def test_the_hpc_tier_is_scoped_to_candidates(rules):
    """Fold the shortlist, not the genome: hours of cluster time for a question this
    route does not ask, and it would not lift the A2 ceiling either."""
    hpc = rules.get(
        "evidence_acquisition.providers.alphafold.execution_tiers.session_trusted_hpc")
    assert hpc["scope"] == "candidates_only"
    assert "A2" in hpc["scope_why"]


def test_the_skill_states_all_three_rules_for_the_hpc_tier():
    import pathlib as _p
    skill = (_p.Path(__file__).resolve().parent.parent / "packaging" / "biomni"
             / "skill" / "rbpfinder" / "SKILL.md").read_text(encoding="utf-8")
    assert "Ask every run" in skill
    assert "Say what leaves" in skill
    assert "Record it" in skill
    # and the thing a second structural route most invites getting wrong
    assert "still ONE family" in skill
    assert "not add a vote" in skill


# ------------------------- the CLI/web-JSON column trap, found by a real HPC-style run

CLI_COLUMNS = ("query,target,fident,alnlen,mismatch,gapopen,qstart,qend,tstart,tend,"
               "evalue,bits,prob,theader,taxname").split(",")
CLI_ROW = "\t".join([
    "X_CDS_0005", "4b1m_A", "1.000", "160", "0", "0", "5", "165", "1", "161",
    "6.437e-30", "1440", "1.000", "4b1m_A CBM66 carbohydrate binding module",
    "Bacillus subtilis"])


def _parse_doc(tmp_path, doc, name="d.json"):
    from rbpfinder.parse import foldseek
    p = tmp_path / name
    p.write_text(json.dumps(doc), encoding="utf-8")
    return foldseek.read(p)


def test_the_converter_lands_every_field(tmp_path):
    """Measured against a real structure-structure search. Of Foldseek's native column
    names only `prob` and `target` coincide with the web-JSON names the parser reads."""
    from rbpfinder.providers.local_foldseek import cli_rows_to_document
    h = _parse_doc(tmp_path, cli_rows_to_document([CLI_ROW], CLI_COLUMNS))[0]
    assert h["probability"] == 1.0
    assert h["evalue"] == 6.437e-30
    assert h["score"] == 1440.0
    assert h["seq_identity"] == 1.0
    assert h["query_span"] == (5, 165)
    assert h["taxon"] == "Bacillus subtilis"
    # theader already starts with the identifier; joining blindly would duplicate it
    assert h["description"] == "CBM66 carbohydrate binding module"


def test_hand_rolling_the_json_loses_everything_but_the_probability(tmp_path):
    """The symptom signature, pinned so it is recognisable rather than only described:
    a probability present and every other field None means someone used Foldseek's
    native column names instead of the converter."""
    naive = {"results": [{"db": "pdb100", "alignments": [[dict(
        zip(CLI_COLUMNS, CLI_ROW.split("\t")))]]}]}
    h = _parse_doc(tmp_path, naive, "naive.json")[0]
    assert h["probability"] == 1.0          # `prob` coincides, so it survives
    assert h["evalue"] is None              # `evalue` vs `eval`
    assert h["score"] is None               # `bits` vs `score`
    assert h["seq_identity"] is None        # `fident` vs `seqId`
    assert h["query_span"] == (None, None)  # `qstart` vs `qStartPos`
    assert h["taxon"] == ""                 # `taxname` vs `taxName`


def test_the_converter_refuses_rows_from_several_queries(tmp_path):
    """One document holds one CDS. Mixing them would attribute a structure to the wrong
    protein, which nothing downstream could detect."""
    from rbpfinder.providers.local_foldseek import cli_rows_to_document
    other = CLI_ROW.replace("X_CDS_0005", "X_CDS_0009", 1)
    with pytest.raises(ValueError, match="wrong protein"):
        cli_rows_to_document([CLI_ROW, other], CLI_COLUMNS)


def test_the_converter_declares_the_route(tmp_path):
    """The second half of the same trap: without a declaration, a structure search whose
    format omitted `prob` would be inferred as ProstT5."""
    from rbpfinder.providers.local_foldseek import cli_rows_to_document
    doc = cli_rows_to_document([CLI_ROW], CLI_COLUMNS)
    assert doc["query_kind"] == "backbone_structure"
    hits = _parse_doc(tmp_path, doc, "declared.json")
    assert structural.query_kind(hits) == "backbone_structure"
