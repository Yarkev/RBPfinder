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

import pytest

from rbpfinder import cli
from rbpfinder.evidence import structural
from rbpfinder.ingest import genbank as genbank_in
import test_end_to_end as e2e

FUNCTIONAL = "7xyz_A Crystal structure of a phage tailspike receptor binding domain"


def _hit(prob=None):
    a = {"target": FUNCTIONAL, "eval": 1e-30, "score": 520.0, "seqId": 0.41,
         "qStartPos": 5, "qEndPos": 195, "dbStartPos": 1, "dbEndPos": 190,
         "qLen": 200, "taxName": "Salmonella phage"}
    if prob is not None:
        a["prob"] = prob
    return a


def _run_with_return(tmp_path, name, prob):
    gbk = tmp_path / "syn.gbk"
    if not gbk.exists():
        e2e._genbank(gbk)
    rows, _meta = genbank_in.read(gbk, phage_id="PV")
    dark = next(r for r in rows
                if (r["product"] or "").lower().startswith("hypothetical"))
    art = tmp_path / ("%s.foldseek.json" % name)
    art.write_text(json.dumps(
        {"results": [{"db": "pdb100", "alignments": [[_hit(prob)]]}]}), encoding="utf-8")
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


def test_a_hit_with_no_probability_is_a_sequence_query():
    """What ProstT5 produces: Foldseek has no backbone to compute a probability from."""
    assert structural.query_kind([{"probability": None}]) == "sequence_3di"


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
    prost = _run_with_return(tmp_path, "prost", prob=None)

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
