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
    assert counts["fiber assembly"] == 1          # the downstream chaperone
    # `complex` was in this list and the first real counts removed it -- see
    # test_complex_was_pruned_from_the_vocabulary.
    assert "complex" not in counts


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
    # `baseplate` alone is what catches it. `initiator` was in the vocabulary and was
    # removed: gp16's PDB title never contained the word, only the cryo-EM reading of it
    # did. The case is still caught, which is why removal cost nothing.
    assert {f["term"] for f in hit["but_hits_describe"]} == {"baseplate"}
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


# ============================== calibration from the first real run (2026-11-21)

def test_complex_was_pruned_from_the_vocabulary(r):
    """First real trigger counts: of 3 hits on 01F2, 2 were chaperone proteins matching
    a Mu title "Complex of Mu phage tail fiber and its chaperone" -- a chaperone hitting
    a fibre-chaperone complex structure is the normal case, not a conflict."""
    terms = r.get(_B + ".structure_annotation_consistency.assembly_context_terms")
    assert "complex" not in terms


def test_pruning_complex_does_not_lose_the_measured_true_signal(r):
    """gp16's real hit title is "Baseplate Complex for Myoviridae Phage XM1" -- it
    carries `baseplate` too, which is why the word could be pruned safely."""
    cands = [{"cds_id": "X_CDS_0019",
              "annotation": {"product": "tail fiber protein"}}]
    records = {"X_CDS_0019": [
        {"family": "T", "tool": "local_foldseek",
         "direction": "supports_apparatus_membership", "strength": "moderate",
         "raw": {"hit_description": "Baseplate Complex for Myoviridae Phage XM1"}}]}
    hit = ps.build(cands, records, [], r)["signals"]["X_CDS_0019"]
    assert "structure_annotation_conflict" in hit
    terms = {f["term"] for f in hit["structure_annotation_conflict"]["but_hits_describe"]}
    assert terms == {"baseplate"}


@pytest.mark.parametrize("product", [
    "tail fiber assembly protein", "tail fibre assembly protein",
    "tail fiber chaperone", "tail assembly chaperone",
])
def test_a_chaperone_does_not_conflict_with_its_own_hit_titles(r, product):
    """Reported from the first real run. The cause was substring matching:
    "tail fiber assembly protein" CONTAINS "tail fiber". Same bug class as the C1/C2
    whole-token fix and `normalize._direction`'s conservative reading of a compound
    name -- a rule this codebase already had and this matcher did not carry over."""
    cands = [{"cds_id": "C_X", "annotation": {"product": product}}]
    records = {"C_X": [
        {"family": "T", "tool": "local_foldseek",
         "direction": "supports_structural_only", "strength": "moderate",
         "raw": {"hit_description":
                 "Complex of Mu phage tail fiber and its chaperone, baseplate"}}]}
    assert "structure_annotation_conflict" not in ps.build(
        cands, records, [], r)["signals"].get("C_X", {})


def test_a_sheath_with_a_chaperone_downstream_is_not_a_fibre(r):
    """Also reported from the first real run: adjacency fired on a tail sheath. A
    chaperone next to a sheath does not make the sheath a fibre."""
    rows = [{"cds_id": "S_1", "product": "tail sheath protein",
             "strand": "+", "start_nt": 1, "end_nt": 1500},
            {"cds_id": "S_2", "product": "tail fiber assembly protein",
             "strand": "+", "start_nt": 1600, "end_nt": 2000}]
    cands = [{"cds_id": "S_1", "annotation": {"product": "tail sheath protein"}}]
    assert ps.build(cands, {"S_1": []}, rows, r)["signals"] == {}


def test_a_dark_matter_candidate_still_gets_the_adjacency_signal(r):
    """The gate is "does this protein positively name a DIFFERENT part", not "does it
    have positive fibre evidence". The test round suggested the latter; it would also
    exclude a hypothetical protein with a chaperone immediately downstream, which is the
    most interesting case this signal has. Excluding only what is named something else
    keeps it."""
    rows = [{"cds_id": "H_1", "product": "hypothetical protein",
             "strand": "+", "start_nt": 1, "end_nt": 2400},
            {"cds_id": "H_2", "product": "tail fiber assembly protein",
             "strand": "+", "start_nt": 2500, "end_nt": 2900}]
    cands = [{"cds_id": "H_1", "annotation": {"product": "hypothetical protein"}}]
    sig = ps.build(cands, {"H_1": []}, rows, r)["signals"]
    assert "fibre_chaperone_adjacency" in sig["H_1"]


def test_the_exclusion_points_at_the_existing_vocabulary(r):
    """One vocabulary to fix, not two. "tail fiber protein" must NOT be on that list, or
    the genuine case would be excluded along with the chaperones."""
    src = r.get(_B + ".self_product_names_another_part.terms_from")
    assert src == "stage4.direction_map.supports_structural_only"
    terms = r.get(src)
    assert "tail sheath" in terms and "fiber assembly" in terms
    assert "tail fiber" not in terms


def test_a_missing_product_produces_no_conflict_signal(r):
    """The first run's report quoted `product_says=None` alongside a firing signal. That
    key does not exist in this output -- the field is `product_claims` -- so it was a
    key-name mismatch, not this. Pinned so the contract is unambiguous either way."""
    cands = [{"cds_id": "N_1", "annotation": {"product": None}}]
    records = {"N_1": [
        {"family": "T", "tool": "t", "direction": "supports_structural_only",
         "strength": "moderate", "raw": {"hit_description": "baseplate wedge"}}]}
    per = ps.build(cands, records, [], r)["signals"].get("N_1", {})
    assert "structure_annotation_conflict" not in per


def test_the_output_field_is_named_product_claims(r):
    """Named here so a consumer can key on it with confidence."""
    cands = [{"cds_id": "P_1", "annotation": {"product": "tail fiber protein"}}]
    records = {"P_1": [
        {"family": "T", "tool": "t", "direction": "supports_apparatus_membership",
         "strength": "moderate", "raw": {"hit_description": "baseplate"}}]}
    hit = ps.build(cands, records, [], r)["signals"]["P_1"]
    assert set(hit["structure_annotation_conflict"]) == {
        "product_claims", "but_hits_describe", "reading", "action"}


# ================================================== GUARDRAIL 5: overfitting is visible

def test_the_vocabulary_records_which_terms_are_unmeasured():
    """The list does not look different from the outside, so the provenance has to be
    written down: one term has fired on real data, two were transcribed from a human
    interpretation of a structure rather than from text the matcher reads."""
    yaml_text = (ROOT / "rules" / "decision_rules.yaml").read_text(encoding="utf-8")
    assert "OVERFITTING AUDIT" in yaml_text
    assert "transcribed from the human EXPLANATION of the case" in yaml_text
    assert "ARCHITECTURE BLIND SPOT" in yaml_text


def test_both_skills_say_a_zero_is_not_a_clean_bill_of_health():
    """The way this overfitting would actually cause harm is an agent reading an empty
    signal block as "nothing to flag" on a phage whose anatomy the vocabulary does not
    cover."""
    text = (ROOT / "packaging" / "biomni" / "skill" / "rbpfinder"
            / "SKILL.md").read_text(encoding="utf-8")
    assert "not a clean bill of health" in text
    assert "myovirus" in text


def test_the_terms_fitted_to_an_interpretation_are_gone(r):
    """`tube initiator` and `initiator` were added because a cryo-EM map says gp16 IS a
    tube initiator. The matcher reads PDB TITLES, and gp16's said "Baseplate Complex for
    Myoviridae Phage XM1" -- so the words were transcribed from the human explanation of
    the case, not from anything in the data. There was no hypothesis for a larger run to
    test, so the remedy was removal rather than measurement."""
    terms = r.get(_B + ".structure_annotation_consistency.assembly_context_terms")
    assert "tube initiator" not in terms
    assert "initiator" not in terms
    yaml_text = (ROOT / "rules" / "decision_rules.yaml").read_text(encoding="utf-8")
    assert "REMOVED 2026-11-21, on provenance rather than on counts" in yaml_text


def test_the_surviving_unmeasured_terms_are_hypotheses_not_transcriptions(r):
    """The five kept terms are literature anatomy: a larger run can falsify them, which
    is exactly what the two removed ones could not offer. They are kept so that the
    scale round measures them rather than so that nobody has to decide."""
    terms = set(r.get(_B + ".structure_annotation_consistency.assembly_context_terms"))
    assert terms == {"baseplate", "wedge", "tail terminator", "assembly chaperone",
                     "hub", "tape measure"}
    yaml_text = (ROOT / "rules" / "decision_rules.yaml").read_text(encoding="utf-8")
    assert "a HYPOTHESIS, which is the difference" in yaml_text


def test_a_sweep_can_calibrate_the_vocabulary_from_run_output_alone():
    """`--aggregate` rolls up the counts every run already records, so a large round
    produces the calibration as a by-product. Without it, `calibration: pending` would be
    a status nobody could ever discharge."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "tnf", ROOT / "packaging" / "comparison" / "term_noise_floor.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "aggregate_runs") and hasattr(mod, "report_aggregate")


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
