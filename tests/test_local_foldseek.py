"""Local Foldseek executor: the whole flow, with a fake `foldseek` binary.

A real foldseek cannot run in CI (binary + multi-GB database + GPU/CPU weights), but the
code around it can be validated end to end by putting a tiny fake `foldseek` on PATH that
emits a canned easy-search table in the exact columns the executor requests. That proves
every link except the real binary's own behaviour: argument assembly, the TSV ->
web-JSON normalisation, the cache, searched-no-hit on disk, and -- through
`structural.build` -- that a pdb100 hit named like an RBP actually produces a
receptor-binding family-T record.

The one thing it cannot prove is that a real foldseek emits these columns; that is pinned
by `--format-output` in stage3.local_foldseek and must be confirmed once on the target
machine.
"""
import json
import os
import stat
import pathlib

import pytest

from rbpfinder.capability import FoldseekCapability
from rbpfinder.config import Rules
from rbpfinder.evidence import structural
from rbpfinder.parse import foldseek as foldseek_parse
from rbpfinder.providers.local_foldseek import LocalFoldseek, StructureDatabase
from rbpfinder import stage3_local

ROOT = pathlib.Path(__file__).resolve().parent.parent


# A fake foldseek: for every query line in the FASTA it is given, it writes one hit row
# whose target description depends on the query id, so different CDS get different
# structural identities. Columns follow stage3.local_foldseek.format_output exactly.
_FAKE_FOLDSEEK = r'''#!/usr/bin/env python3
import sys, pathlib
# argv: easy-search <query.faa> <db> <res> <tmp> --format-output ... [-s ..] [--threads ..] [--prostt5-model ..]
args = sys.argv[1:]
assert args[0] == "easy-search", args
query, db, res = args[1], args[2], args[3]
ids = []
for line in pathlib.Path(query).read_text().splitlines():
    if line.startswith(">"):
        ids.append(line[1:].strip())
# db prefix basename is the label-ish name; we just echo "pdb100" style rows
rows = []
for qid in ids:
    if "RBP" in qid:
        theader = "Crystal structure of a tail fiber receptor binding protein"
    elif "BASE" in qid:
        theader = "Baseplate wedge protein, structural component"
    else:
        theader = None  # no hit for this query
    if theader is None:
        continue
    rows.append("\t".join([qid, "1abc_A", theader, "0.98", "1e-18", "320.0",
                           "0.41", "5", "250", "1", "245", "300", "Escherichia phage"]))
pathlib.Path(res).write_text("\n".join(rows) + ("\n" if rows else ""))
'''


@pytest.fixture()
def fake_foldseek(tmp_path):
    exe = tmp_path / "foldseek"
    exe.write_text(_FAKE_FOLDSEEK)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    # make it runnable as a program: prepend a python shebang-friendly wrapper dir
    return exe


@pytest.fixture(scope="module")
def rules():
    return Rules(ROOT / "rules" / "decision_rules.yaml")


def _records():
    return [
        {"cds_id": "P_CDS_0009", "translation": "M" + "A" * 299, "length_aa": 300},
        {"cds_id": "P_CDS_0008", "translation": "M" + "C" * 299, "length_aa": 300},
        {"cds_id": "P_CDS_0006", "translation": "M" + "D" * 299, "length_aa": 300},
    ]


def _rename(cds, mapping):
    for r in cds:
        r["cds_id"] = mapping.get(r["cds_id"], r["cds_id"])
    return cds


def test_capability_precedence_is_authority(tmp_path):
    missing = tmp_path / "nope"
    cap = FoldseekCapability(foldseek=str(missing))
    assert not cap["foldseek"].available
    with pytest.raises(Exception):
        cap.require("foldseek")


def test_normalises_to_web_json_and_builds_rb_record(fake_foldseek, rules, tmp_path):
    # name the query so the fake emits an RBP-titled hit
    recs = [{"cds_id": "P_CDS_0009_RBP", "translation": "M" + "A" * 299, "length_aa": 300}]
    db = StructureDatabase("pdb100", str(tmp_path / "db" / "pdb100"))
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "pdb100.dbtype").write_text("x")

    lf = LocalFoldseek(databases=[db], prostt5_model=str(tmp_path / "prostt5"),
                       foldseek_exe=str(fake_foldseek),
                       cache_dir=str(tmp_path / "cache"), rules=rules)
    docs = lf.search_many(recs)
    doc = docs["P_CDS_0009_RBP"]
    assert doc["results"][0]["db"] == "pdb100"

    # the existing web-JSON parser reads it unchanged
    hits = foldseek_parse.read_json_doc(doc) if hasattr(foldseek_parse, "read_json_doc") \
        else _read_doc(foldseek_parse, doc, tmp_path)
    assert hits and hits[0]["database"] == "pdb100"
    assert "receptor binding" in hits[0]["description"].lower()

    # and structural.build turns it into a receptor-binding family-T record
    t_rec, _cons = structural.build(recs[0], hits, rules, "local_foldseek")
    assert t_rec["family"] == "T"
    assert t_rec["direction"] == "supports_receptor_binding"


def _read_doc(foldseek_parse, doc, tmp_path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps(doc))
    return foldseek_parse.read(p)


def test_searched_no_hit_is_written_not_omitted(fake_foldseek, rules, tmp_path):
    # a query with neither RBP nor BASE in its id gets no hit from the fake
    cds = [{"cds_id": "X_CDS_0042", "translation": "M" + "E" * 150, "length_aa": 151}]
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "pdb100.dbtype").write_text("x")
    rows, prov = stage3_local.prepare(
        cds, rules, FoldseekCapability(foldseek=str(fake_foldseek)), str(tmp_path),
        prostt5_model=str(tmp_path / "prostt5"),
        explicit_dbs=[("pdb100", str(tmp_path / "db" / "pdb100"))])
    assert len(rows) == 1
    art = pathlib.Path(rows[0]["path"])
    assert art.exists()                       # searched-no-hit is on disk, not missing
    doc = json.loads(art.read_text())
    assert doc["results"][0]["alignments"] == [[]]   # empty, but present
    assert prov["cds_with_hits"] == 0


def test_unknown_db_label_is_refused(rules, tmp_path):
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "weird.dbtype").write_text("x")
    use, decisions = stage3_local.resolve_structure_databases(
        rules, explicit=[("not_a_real_db", str(tmp_path / "db" / "weird"))])
    assert use == []
    assert decisions[0]["reason"] == "unknown_label"


def test_bare_path_without_label_is_an_error(rules, tmp_path):
    use, decisions = stage3_local.resolve_structure_databases(
        rules, explicit=[(None, str(tmp_path / "pdb100"))])
    assert use == []
    assert decisions[0]["reason"] == "no_label"


def test_env_var_parses_label_path_pairs(rules, tmp_path):
    (tmp_path / "db").mkdir()
    for name in ("pdb100", "cath50"):
        (tmp_path / "db" / (name + ".dbtype")).write_text("x")
    env = {"RBPFINDER_FOLDSEEK_DB":
           "pdb100=%s,cath50=%s" % (tmp_path / "db" / "pdb100",
                                    tmp_path / "db" / "cath50")}
    use, decisions = stage3_local.resolve_structure_databases(rules, env=env)
    assert sorted(d.label for d in use) == ["cath50", "pdb100"]


# A fake that gives a strong RBP-titled structural hit to the tail-fibre CDS (id ending
# 0009 in the synthetic genome), so a full run can show the tier actually move.
_FAKE_FOLDSEEK_RBP0009 = r'''#!/usr/bin/env python3
import sys, pathlib
args = sys.argv[1:]
query, res = args[1], args[3]
rows = []
for line in pathlib.Path(query).read_text().splitlines():
    if line.startswith(">"):
        qid = line[1:].strip()
        if qid.endswith("0009"):
            rows.append("\t".join([qid, "1abc_A",
                "Crystal structure of tail fiber receptor binding protein",
                "0.99", "1e-25", "400.0", "0.45", "3", "290", "1", "288", "300",
                "Escherichia phage"]))
pathlib.Path(res).write_text("\n".join(rows) + ("\n" if rows else ""))
'''


def test_full_run_structure_lifts_tail_fibre_tier(rules, tmp_path, monkeypatch):
    """End-to-end: local Foldseek wired through the CLI adds a family-T record that moves
    the tail-fibre CDS from R2 to R1. This is the whole point -- structural evidence is
    what lets a real RBP reach the shortlist with confidence."""
    import test_end_to_end as e2e
    from rbpfinder import cli

    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)

    # structure DB + fake foldseek + a (never-read) prostt5 dir
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "pdb100.dbtype").write_text("x")
    (tmp_path / "prostt5").mkdir()
    exe = tmp_path / "foldseek"
    exe.write_text(_FAKE_FOLDSEEK_RBP0009)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)

    def tier_of(outdir, cds_id):
        import csv as _csv
        with (pathlib.Path(outdir) / "all_CDS_screening.tsv").open() as fh:
            for row in _csv.DictReader(fh, delimiter="\t"):
                if row["cds_id"] == cds_id:
                    return row["tier"]
        return None

    base = tmp_path / "base"
    cli.main(["--genbank", str(gbk), "--phage-id", "SYN", "--out", str(base),
              "--no-local-stage2", "--no-local-stage3", "--quiet-progress"])
    base_tier = tier_of(base, "SYN_CDS_0009")

    withfs = tmp_path / "withfs"
    cli.main(["--genbank", str(gbk), "--phage-id", "SYN", "--out", str(withfs),
              "--no-local-stage2", "--quiet-progress",
              "--foldseek", str(exe),
              "--foldseek-db", "pdb100=%s" % (tmp_path / "db" / "pdb100"),
              "--prostt5-model", str(tmp_path / "prostt5")])
    fs_tier = tier_of(withfs, "SYN_CDS_0009")

    assert base_tier.startswith("R2")
    assert fs_tier.startswith("R1")

    # provenance records the local Stage 3 run
    result = json.loads((withfs / "run_result.json").read_text())
    assert result["stage3_local"]["mode"] == "local_foldseek"
    assert result["stage3_local"]["cds_with_hits"] == 1


def test_import_only_refuses_local_foldseek(rules, tmp_path):
    """A search is a search: import_only parses existing results and runs none."""
    from rbpfinder.policy import DataPolicy
    from rbpfinder.providers.local_foldseek import PROVIDER_ID
    allowed, reason = DataPolicy(rules, mode="import_only").allows(PROVIDER_ID)
    assert not allowed
    for mode in ("private", "remote_allowed"):
        ok, _ = DataPolicy(rules, mode=mode, run_authorised=True).allows(PROVIDER_ID)
        assert ok


def test_cache_prevents_a_second_search(fake_foldseek, rules, tmp_path):
    recs = [{"cds_id": "P_CDS_0001_RBP", "translation": "M" + "A" * 99, "length_aa": 100}]
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "pdb100.dbtype").write_text("x")
    db = StructureDatabase("pdb100", str(tmp_path / "db" / "pdb100"))
    cache = str(tmp_path / "cache")
    lf = LocalFoldseek(databases=[db], prostt5_model=None,
                       foldseek_exe=str(fake_foldseek), cache_dir=cache, rules=rules)
    lf.search_many(recs)
    cached = list(pathlib.Path(cache).glob("*.json"))
    assert cached
    # break the executable; a cached query must still resolve
    lf2 = LocalFoldseek(databases=[db], prostt5_model=None,
                        foldseek_exe=str(fake_foldseek), cache_dir=cache, rules=rules)
    lf2.exe = pathlib.Path(tmp_path / "does_not_exist")
    docs = lf2.search_many(recs)
    assert "P_CDS_0001_RBP" in docs
