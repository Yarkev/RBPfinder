"""The Biomni custom-tool wrapper, end to end through the real CLI.

Biomni itself is not a dependency, so `register(agent)` is checked against a stub that
records what a real A1 agent's add_tool / add_software / add_data would receive. The tool
function is run for real: it shells out to the installed `rbpfinder`, so this also proves
the wrapper's command assembly and its reading of run_result.json.
"""
import pathlib
import stat
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging" / "biomni"))

rbpfinder_biomni = pytest.importorskip("rbpfinder_biomni")


_FAKE_FOLDSEEK = r'''#!/usr/bin/env python3
import sys, pathlib
args = sys.argv[1:]
query, res = args[1], args[3]
rows = []
for line in pathlib.Path(query).read_text().splitlines():
    if line.startswith(">") and line.strip().endswith("0009"):
        rows.append("\t".join([line[1:].strip(), "1abc_A",
            "Crystal structure of tail fiber receptor binding protein",
            "0.99", "1e-25", "400.0", "0.45", "3", "290", "1", "288", "300", "phage"]))
pathlib.Path(res).write_text("\n".join(rows) + ("\n" if rows else ""))
'''


class _StubAgent:
    def __init__(self):
        self.tools, self.software, self.data = [], {}, {}

    def add_tool(self, fn):
        self.tools.append(fn.__name__)

    def add_software(self, d):
        self.software.update(d)

    def add_data(self, d):
        self.data.update(d)


def test_register_wires_tool_software_and_data(tmp_path, monkeypatch):
    monkeypatch.setenv("RBPFINDER_FOLDSEEK_DB", "pdb100=%s" % (tmp_path / "pdb100"))
    monkeypatch.setenv("RBPFINDER_PROSTT5_MODEL", str(tmp_path / "prostt5"))
    agent = _StubAgent()
    rbpfinder_biomni.register(agent)
    # the standardised flow: plan the route, validate the annotation, then analyse
    assert agent.tools == ["rbpfinder_workflow_plan", "check_annotation",
                           "run_rbpfinder"]
    assert "rbpfinder" in agent.software and "foldseek" in agent.software
    assert any("pdb100" in v for v in agent.data.values())
    assert any("ProstT5" in v for v in agent.data.values())


def test_run_rbpfinder_returns_primary_and_honest_completeness(tmp_path, monkeypatch):
    import test_end_to_end as e2e      # reuse the synthetic genome builder

    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "pdb100.dbtype").write_text("x")
    (tmp_path / "prostt5").mkdir()
    exe = tmp_path / "foldseek"
    exe.write_text(_FAKE_FOLDSEEK)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    monkeypatch.setenv("RBPFINDER_FOLDSEEK", str(exe))

    out = rbpfinder_biomni.run_rbpfinder(
        str(gbk), "SYN", str(tmp_path / "run"),
        foldseek_db="pdb100=%s" % (tmp_path / "db" / "pdb100"),
        prostt5_model=str(tmp_path / "prostt5"))

    assert out["status"] == "ok"
    assert [c["cds_id"] for c in out["primary"]] == ["SYN_CDS_0009"]
    assert out["primary"][0]["tier"] == "R1-E3"
    assert out["primary"][0]["annotation"] == "tail fiber protein"
    # completeness is reported as produced, not smoothed
    assert out["completeness"]["state"] in ("complete", "partial", "limited")
    assert out["completeness"]["reasons"]
    assert "Do not expand Rescue" in out["note"]


def test_run_rbpfinder_reports_input_error(tmp_path):
    """A malformed annotation is caught by the precondition gate, before any analysis."""
    bad = tmp_path / "not.gbk"
    bad.write_text("this is not a genbank file")
    out = rbpfinder_biomni.run_rbpfinder(str(bad), "X", str(tmp_path / "out"))
    assert out["status"] == "error"
    assert out["stage"] == "annotation_check"
    assert out["problems"]


# --- the standardised workflow: plan -> validate -> run -------------------------------

def test_plan_with_no_mode_asks_the_user_and_defaults_to_fast():
    """The route costs an hour of CPU, so the choice is the user's, not the agent's."""
    plan = rbpfinder_biomni.rbpfinder_workflow_plan()
    assert plan["next_action"] == "ask_user"
    assert plan["default_if_no_preference"] == "fast"
    assert set(plan["options"]) == {"fast", "accurate"}
    assert "phold" in plan["question"]


def test_plan_fast_does_not_run_phold():
    plan = rbpfinder_biomni.rbpfinder_workflow_plan("fast")
    assert plan["mode"] == "fast"
    joined = " ".join(plan["annotation_commands"])
    assert "pharokka" in joined
    assert "phold" not in joined
    assert plan["deliverable"].endswith("pharokka.gbk")


def test_plan_accurate_adds_phold_and_hands_over_its_genbank():
    plan = rbpfinder_biomni.rbpfinder_workflow_plan("accurate")
    joined = " ".join(plan["annotation_commands"])
    assert "pharokka" in joined and "phold run" in joined
    # phold's GenBank, never its TSV: the table has no protein sequence
    assert plan["deliverable"].endswith("phold.gbk")
    assert "TSV" in plan["do_not_hand_over"]
    assert "1-2 h" in plan["compute"]


def test_plan_rejects_an_unknown_mode():
    out = rbpfinder_biomni.rbpfinder_workflow_plan("thorough")
    assert out["status"] == "error"


def test_check_annotation_accepts_a_good_genbank(tmp_path):
    import test_end_to_end as e2e
    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    info = rbpfinder_biomni.check_annotation(genbank_path=str(gbk))
    assert info["usable"] is True
    assert info["cds_count"] > 0
    assert info["with_translation"] == info["cds_count"]
    assert info["supports_local_structure_search"] is True


def test_check_annotation_refuses_a_bare_fasta(tmp_path):
    bare = tmp_path / "genome.fasta"
    bare.write_text(">contig1\nACGTACGTACGT\n")
    info = rbpfinder_biomni.check_annotation(genbank_path=str(bare))
    assert info["usable"] is False
    assert info["problems"]


def test_run_refuses_before_analysing_when_annotation_is_unusable(tmp_path):
    bare = tmp_path / "genome.fasta"
    bare.write_text(">contig1\nACGTACGTACGT\n")
    out = rbpfinder_biomni.run_rbpfinder(str(bare), "X", str(tmp_path / "never"))
    assert out["status"] == "error"
    assert out["stage"] == "annotation_check"
    assert not (tmp_path / "never").exists()      # nothing was analysed


def test_run_reports_the_mode_and_the_structure_search(tmp_path, monkeypatch):
    import test_end_to_end as e2e
    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    (tmp_path / "db").mkdir()
    (tmp_path / "db" / "pdb100.dbtype").write_text("x")
    (tmp_path / "prostt5").mkdir()
    exe = tmp_path / "foldseek"
    exe.write_text(_FAKE_FOLDSEEK)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)
    monkeypatch.setenv("RBPFINDER_FOLDSEEK", str(exe))

    out = rbpfinder_biomni.run_rbpfinder(
        str(gbk), "SYN", str(tmp_path / "run"), mode="fast",
        foldseek_db="pdb100=%s" % (tmp_path / "db" / "pdb100"),
        prostt5_model=str(tmp_path / "prostt5"))
    assert out["mode"] == "fast"
    s = out["local_structure_search"]
    assert s["ran"] is True
    assert s["scope"] == "nominated_pool"
    assert s["cds_searched"] < len(e2e.PRODUCTS)       # pool-scoped, not genome-wide
