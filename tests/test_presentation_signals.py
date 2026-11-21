"""Presentation signals must be inert, and that is proved rather than promised.

Added with the signals themselves (2026-11-21), as the first of four guardrails the
maintainer attached to allowing a new vocabulary into `decision_rules.yaml`. The red line
was never "do not touch the file" but "calibration-bearing values must not migrate
silently" -- so the burden here is to demonstrate that this block cannot affect a
conclusion.

The central test is `test_an_adversarial_vocabulary_changes_nothing`: it fills every word
list with terms that match every product name and every hit description, so every signal
fires on every candidate, and then asserts the pool, the families, the tiers, the ranking
and Primary/Rescue are byte-identical to a run with the signals switched off. If that test
can be made to fail, this block has escaped its layer and the feature must come out.
"""
import copy
import json
import pathlib

import pytest

from rbpfinder import cli
from rbpfinder.config import Rules
from rbpfinder.output import presentation_signals as ps

import test_end_to_end as e2e

ROOT = pathlib.Path(__file__).resolve().parent.parent
_B = "presentation_signals"


@pytest.fixture()
def r():
    return Rules(ROOT / "rules" / "decision_rules.yaml")


def _run(tmp_path, name, extra=()):
    gbk = tmp_path / "syn.gbk"
    if not gbk.exists():
        e2e._genbank(gbk)
    out = tmp_path / name
    assert cli.main(["--genbank", str(gbk), "--phage-id", "SYN", "--out", str(out),
                     "--no-local-stage2", "--quiet-progress", *extra]) == 0
    return json.loads((out / "run_result.json").read_text(encoding="utf-8"))


def _conclusions(d):
    """Everything a signal is forbidden from touching."""
    return {
        "primary": sorted(d["primary_set"]),
        "rescue": sorted(d["rescue_set"]),
        "completeness": d["completeness_assessment"],
        "order": [c["cds_id"] for c in d["candidates"]],
        "tiers": {c["cds_id"]: (c["tier_r"], c["tier_e"]) for c in d["candidates"]},
        "families": {c["cds_id"]: sorted(c["independent_families"])
                     for c in d["candidates"]},
        "channels": {c["cds_id"]: sorted(c["nomination_channels"])
                     for c in d["candidates"]},
        "roles": {c["cds_id"]: c["protein_role"] for c in d["candidates"]},
    }


# =========================================================== GUARDRAIL 1: dormancy

def test_an_adversarial_vocabulary_changes_nothing(tmp_path, monkeypatch):
    """The guardrail. Every term matches everything, so every signal fires -- and not one
    conclusion may move."""
    baseline = _run(tmp_path, "off")

    real_build = ps.build

    def saturated(candidates, records_by_id, cds_rows, rules, is_circular=False):
        # A vocabulary that cannot fail to match: single letters appear in every
        # product name and every hit description in the fixture.
        everything = ["a", "e", "i", "o", "protein", " "]
        rules.set_override(_B + ".structure_annotation_consistency.part_name_terms",
                           everything)
        rules.set_override(_B + ".structure_annotation_consistency.assembly_context_terms",
                           everything)
        rules.set_override(_B + ".fibre_chaperone_adjacency.chaperone_terms_subset",
                           everything)
        rules.set_override(_B + ".fibre_chaperone_adjacency.downstream_window_cds", 99)
        rules.set_override(_B + ".direction_conflict.conflicting_directions",
                           ["supports_structural_only", "supports_non_rbp_identity",
                            "supports_apparatus_membership", "uninformative"])
        return real_build(candidates, records_by_id, cds_rows, rules,
                          is_circular=is_circular)

    monkeypatch.setattr(ps, "build", saturated)
    loud = _run(tmp_path, "loud")

    assert _conclusions(loud) == _conclusions(baseline)
    # ... and the saturated vocabulary really did fire, or this proves nothing.
    assert loud["presentation_signals"]["candidates_with_any_signal"] > 0


def test_the_signals_do_not_mutate_what_they_read(r, tmp_path):
    """A hint that edited a record in place would reach the evidence chain through the
    back door, since records are written to the run output."""
    d = _run(tmp_path, "m")
    cands = d["candidates"]
    cds = [{"cds_id": c["cds_id"], "product": c["annotation"]["product"],
            "strand": "+", "start_nt": i * 100, "end_nt": i * 100 + 90}
           for i, c in enumerate(cands)]
    records = {c["cds_id"]: [{"family": "D", "tool": "t", "direction":
                              "supports_receptor_binding", "strength": "moderate",
                              "raw": {"hit_description": "baseplate wedge protein"}}]
               for c in cands}
    before = copy.deepcopy((cands, cds, records))
    ps.build(cands, records, cds, r)
    assert (cands, cds, records) == before


def test_the_block_is_computed_after_the_candidate_sets_are_fixed():
    """Structural, not behavioural: placed after ranking there is nothing downstream for
    a hint to influence, whatever a future edit to this module does."""
    src = (ROOT / "rbpfinder" / "cli.py").read_text(encoding="utf-8")
    assert src.index('run_result["presentation_signals"]') > src.index(
        "ranked = cand_out.rank(")
    assert src.index('run_result["presentation_signals"]') > src.index(
        "primary, rescue = cand_out.assign_sets(")


# =========================================================== GUARDRAIL 2: calibration

def test_the_block_is_marked_pending_calibration(r):
    """Same status semantics as the tiers: expert-derived starting point, no fitted
    trigger rate yet."""
    assert r.get(_B + ".calibration") == "pending"
    assert r.get(_B + ".layer") == "presentation_only"
    assert "nomination" in (r.get(_B + ".forbidden") or "")


def test_the_run_output_carries_the_disclaimer(tmp_path):
    block = _run(tmp_path, "d")["presentation_signals"]
    assert block["calibration"] == "pending"
    assert "No signal here changed a tier" in block["disclaimer"]


# ======================================================= GUARDRAIL 3: observable rate

def test_trigger_counts_are_reported_per_term(r):
    """calibration: pending is only honest if the rate can be measured. A hint firing on
    every candidate is noise; one that never fires is dead weight."""
    cds = [
        {"cds_id": "P_CDS_0001", "product": "tail fiber protein",
         "strand": "+", "start_nt": 1, "end_nt": 900},
        {"cds_id": "P_CDS_0002", "product": "tail fiber assembly protein",
         "strand": "+", "start_nt": 1000, "end_nt": 1500},
    ]
    cands = [{"cds_id": "P_CDS_0001", "annotation": {"product": "tail fiber protein"}}]
    records = {"P_CDS_0001": [
        {"family": "S", "tool": "blastp", "direction": "supports_receptor_binding",
         "strength": "moderate",
         "raw": {"hit_description": "baseplate wedge initiator complex"}}]}
    block = ps.build(cands, records, cds, r)
    counts = block["term_trigger_counts"]
    assert counts["baseplate"] == 1
    assert counts["wedge"] == 1
    assert counts["complex"] == 1
    assert counts["fiber assembly"] == 1          # the downstream chaperone


def test_complex_is_in_the_vocabulary_and_flagged_as_the_one_to_watch(r):
    """It was named by the test round, not shown to discriminate. PDB titles say
    "complex" constantly, so it is the first candidate for pruning."""
    terms = r.get(_B + ".structure_annotation_consistency.assembly_context_terms")
    assert "complex" in terms
    yaml_text = (ROOT / "rules" / "decision_rules.yaml").read_text(encoding="utf-8")
    assert "`complex` is the one to watch" in yaml_text


# ============================================================ the checks themselves

def test_the_measured_false_positive_shape_is_flagged(r):
    """01F2 gp16: product "tail fiber protein", cryo-EM says tube initiator. D and S
    agreed on the part name, so no family could catch it."""
    cds = [{"cds_id": "X_CDS_0016", "product": "tail fiber protein",
            "strand": "+", "start_nt": 1, "end_nt": 780}]
    cands = [{"cds_id": "X_CDS_0016",
              "annotation": {"product": "tail fiber protein"}}]
    records = {"X_CDS_0016": [
        {"family": "D", "tool": "phold_prostt5",
         "direction": "supports_receptor_binding", "strength": "moderate",
         "raw": {"hit_description": "tail fiber protein"}},
        {"family": "T", "tool": "local_foldseek",
         "direction": "supports_apparatus_membership", "strength": "moderate",
         "raw": {"hit_description": "baseplate tube initiator"}}]}
    sig = ps.build(cands, records, cds, r)["signals"]["X_CDS_0016"]
    hit = sig["structure_annotation_conflict"]
    assert hit["product_claims"] == ["tail fiber"]
    assert {f["term"] for f in hit["but_hits_describe"]} >= {"baseplate", "initiator"}
    assert "no tier was changed" in hit["action"]


def test_a_genuine_fibre_is_not_flagged(r):
    """The control. A fibre whose hits also describe a fibre must stay quiet, or the
    hint fires on everything and means nothing."""
    cds = [{"cds_id": "X_CDS_0009", "product": "tail fiber protein",
            "strand": "+", "start_nt": 1, "end_nt": 2958}]
    cands = [{"cds_id": "X_CDS_0009",
              "annotation": {"product": "tail fiber protein"}}]
    records = {"X_CDS_0009": [
        {"family": "S", "tool": "blastp", "direction": "supports_receptor_binding",
         "strength": "strong",
         "raw": {"hit_description": "host specificity protein J, receptor binding"}}]}
    assert ps.build(cands, records, cds, r)["signals"] == {}


def test_a_tailspike_is_not_in_scope(r):
    """`tailspike` is deliberately a receptor-binding term, not a part name: one protein
    that both sits in the tail and recognises the receptor. The part-name list must not
    quietly re-litigate that."""
    terms = r.get(_B + ".structure_annotation_consistency.part_name_terms")
    assert not any("spike" in t for t in terms)


def test_the_chaperone_signal_follows_the_strand(r):
    """Fibre and chaperone are co-transcribed, so for a reverse-strand gene the chaperone
    is at LOWER coordinates. Reading downstream as "higher coordinate" would miss every
    reverse-strand fibre."""
    fwd = [{"cds_id": "A_CDS_0001", "product": "tail fiber protein",
            "strand": "+", "start_nt": 1, "end_nt": 900},
           {"cds_id": "A_CDS_0002", "product": "tail fiber assembly protein",
            "strand": "+", "start_nt": 1000, "end_nt": 1400}]
    rev = [{"cds_id": "B_CDS_0001", "product": "tail fiber assembly protein",
            "strand": "-", "start_nt": 1, "end_nt": 400},
           {"cds_id": "B_CDS_0002", "product": "tail fiber protein",
            "strand": "-", "start_nt": 500, "end_nt": 1400}]
    for rows, cid in ((fwd, "A_CDS_0001"), (rev, "B_CDS_0002")):
        cands = [{"cds_id": cid, "annotation": {"product": "tail fiber protein"}}]
        sig = ps.build(cands, {cid: []}, rows, r)["signals"]
        assert "fibre_chaperone_adjacency" in sig.get(cid, {}), cid


def test_the_chaperone_signal_ignores_the_opposite_strand(r):
    """A neighbour on the other strand is a different operon, not this fibre's helper."""
    rows = [{"cds_id": "C_CDS_0001", "product": "tail fiber protein",
             "strand": "+", "start_nt": 1, "end_nt": 900},
            {"cds_id": "C_CDS_0002", "product": "tail fiber assembly protein",
             "strand": "-", "start_nt": 1000, "end_nt": 1400}]
    cands = [{"cds_id": "C_CDS_0001",
              "annotation": {"product": "tail fiber protein"}}]
    assert ps.build(cands, {"C_CDS_0001": []}, rows, r)["signals"] == {}


def test_the_chaperone_signal_says_it_is_not_a_family(r):
    """The maintainer's constraint, carried in the output rather than only in the rules:
    the C1 positional evidence for this CDS is already an independent family."""
    rows = [{"cds_id": "D_CDS_0001", "product": "tail fiber protein",
             "strand": "+", "start_nt": 1, "end_nt": 900},
            {"cds_id": "D_CDS_0002", "product": "fiber assembly protein",
             "strand": "+", "start_nt": 1000, "end_nt": 1400}]
    cands = [{"cds_id": "D_CDS_0001",
              "annotation": {"product": "tail fiber protein"}}]
    sig = ps.build(cands, {"D_CDS_0001": []}, rows, r)["signals"]["D_CDS_0001"]
    action = sig["fibre_chaperone_adjacency"]["action"]
    assert "NOT a family" in action and "counted twice" in action


def test_the_direction_conflict_check_needs_no_new_vocabulary(r):
    """The cheap half: it reads directions already assigned. What it catches is a T
    record fighting a D record -- which known issue #4 says the evidence model cannot
    express, since one representative per family cannot carry both readings."""
    cands = [{"cds_id": "E_CDS_0005", "annotation": {"product": "hypothetical protein"}}]
    records = {"E_CDS_0005": [
        {"family": "D", "tool": "phold_prostt5",
         "direction": "supports_receptor_binding", "strength": "moderate",
         "raw": {"hit_description": "receptor binding protein"}},
        {"family": "T", "tool": "local_foldseek",
         "direction": "supports_structural_only", "strength": "moderate",
         "raw": {"hit_description": "tail tube"}}]}
    sig = ps.build(cands, records, [], r)["signals"]["E_CDS_0005"]
    hit = sig["direction_conflict"]
    assert [x["family"] for x in hit["receptor_binding_from"]] == ["D"]
    assert "supports_structural_only" in hit["contradicted_by"]


def test_the_direction_conflict_check_would_not_have_caught_gp16(r):
    """Stated as a test so the limitation cannot be forgotten: on 01F2 gp16, D and S
    agreed on the part name and only the external structure disagreed. This check is
    additional to the description check, never a substitute for it."""
    cands = [{"cds_id": "F_CDS_0016",
              "annotation": {"product": "tail fiber protein"}}]
    records = {"F_CDS_0016": [
        {"family": "D", "tool": "phold_prostt5",
         "direction": "supports_receptor_binding", "strength": "moderate",
         "raw": {"hit_description": "tail fiber protein"}},
        {"family": "S", "tool": "blastp", "direction": "supports_receptor_binding",
         "strength": "moderate", "raw": {"hit_description": "tail fiber protein"}}]}
    per = ps.build(cands, records, [], r)["signals"].get("F_CDS_0016", {})
    assert "direction_conflict" not in per


def test_every_check_can_be_switched_off(r):
    cds = [{"cds_id": "G_CDS_0001", "product": "tail fiber protein",
            "strand": "+", "start_nt": 1, "end_nt": 900}]
    cands = [{"cds_id": "G_CDS_0001",
              "annotation": {"product": "tail fiber protein"}}]
    records = {"G_CDS_0001": [
        {"family": "T", "tool": "local_foldseek",
         "direction": "supports_structural_only", "strength": "moderate",
         "raw": {"hit_description": "baseplate wedge"}}]}
    for check in ("structure_annotation_consistency", "fibre_chaperone_adjacency",
                  "direction_conflict"):
        r.set_override("%s.%s.enabled" % (_B, check), False)
    assert ps.build(cands, records, cds, r)["signals"] == {}
