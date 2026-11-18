"""The vocabulary modes: what each one changes, and what none of them may change.

Three readers answer "does this text support receptor binding?" from three vocabularies.
`vocabulary.mode` selects which is authoritative so the difference can be measured one
step at a time. `current` is the default and must reproduce the frozen behaviour exactly.

Every term listed here was checked against the tree, not estimated.
"""
import pytest

from rbpfinder.config import Rules
from rbpfinder.evidence import normalize, ontology, terms

_RB = "supports_receptor_binding"
_APP = "supports_apparatus_membership"
_STRUCT = "supports_structural_only"

GP65 = "tail sheath and receptor binding protein"


@pytest.fixture()
def r():
    """A fresh Rules per test: set_override mutates the instance."""
    import pathlib
    return Rules(pathlib.Path(__file__).resolve().parent.parent
                 / "rules" / "decision_rules.yaml")


def _mode(r, m):
    r.set_override("vocabulary.mode", m)
    return r


# --------------------------------------------------------------- the default is frozen

def test_default_mode_is_current(r):
    assert r.get("vocabulary.mode") == "current"
    assert terms.mode(r) == "current"


def test_declared_modes_match_what_the_code_accepts(r):
    assert r.get("vocabulary.modes") == ["current", "explicit_guard", "unified"]


@pytest.mark.parametrize("product,direction", [
    ("tail fiber protein", _RB),
    ("tip attachment protein", _RB),
    ("tailspike protein", _RB),
    (GP65, _RB),
    ("tail fiber assembly protein", _STRUCT),
    ("portal protein", "supports_non_rbp_identity"),
    ("hypothetical protein", "uninformative"),
])
def test_current_mode_reproduces_the_frozen_product_name_reading(r, product, direction):
    assert normalize._direction(product, _mode(r, "current")) == direction


# ------------------------------------------- explicit_guard: one rule, applied everywhere

def test_explicit_claim_is_vetoed_by_a_structural_word_only_in_current(r):
    """`dual_role_precedence`: "a structural name may never veto a positive binding
    result". The exception lived in the product-name reader alone, so a homolog described
    like N4 gp65 was demoted on two of the three paths."""
    cat, _ = terms.classify(GP65, _mode(r, "current"))
    assert terms.direction_for(cat) == _STRUCT          # the defect, frozen

    for m in ("explicit_guard", "unified"):
        cat, _ = terms.classify(GP65, _mode(r, m))
        assert terms.direction_for(cat) == _RB, m


def test_the_ontology_reader_also_honours_the_explicit_claim(r):
    """Adopting the ontology without this guard would have regressed gp65."""
    assert ontology.classify(GP65, r)["direction"] == _RB


def test_a_compound_title_without_an_explicit_claim_stays_conservative(r):
    """The guard is narrow: it covers a functional CLAIM, not a co-occurring word."""
    for m in ("current", "explicit_guard", "unified"):
        cat, _ = terms.classify("tail fiber protein, collagen repeat", _mode(r, m))
        assert terms.direction_for(cat) == _STRUCT, m


def test_explicit_guard_changes_no_product_name_reading(r):
    """It is a homolog-reader fix; the product-name reader always had the exception."""
    for product in ("tail fiber protein", "tip attachment protein", "tailspike protein",
                    GP65, "tail fiber assembly protein", "portal protein"):
        a = normalize._direction(product, _mode(r, "current"))
        b = normalize._direction(product, _mode(r, "explicit_guard"))
        assert a == b, product


# ----------------------------------------- unified: three curated terms, and no others

@pytest.mark.parametrize("product", [
    "tail fiber protein", "tail fibre protein", "putative tail fiber protein",
    "tip attachment protein",
])
def test_unified_reads_part_names_as_apparatus_not_binding(r, product):
    """The measured 2026-08 decision, now applied to a CDS's own product name too: the
    whole fibre cluster shares the annotation "tail fiber protein", so it identifies the
    APPARATUS and cannot discriminate receptor binding inside it."""
    assert normalize._direction(product, _mode(r, "current")) == _RB
    assert normalize._direction(product, _mode(r, "unified")) == _APP


@pytest.mark.parametrize("product", [
    "tailspike protein", "tail spike protein", GP65,
    "receptor binding protein", "depolymerase", "endosialidase",
])
def test_unified_keeps_the_readings_that_must_not_move(r, product):
    """A tailspike is one protein that both sits in the tail and binds the receptor --
    deliberately mapped to receptor binding, unlike `tail fiber`. And an explicit claim
    stands. These are the controls: if any of them moves, the change is wrong."""
    assert normalize._direction(product, _mode(r, "unified")) == _RB


@pytest.mark.parametrize("product,direction", [
    ("portal protein", "supports_non_rbp_identity"),
    ("hypothetical protein", "uninformative"),
    ("tail protein", _STRUCT),
    ("baseplate protein", _STRUCT),
])
def test_unified_leaves_non_binding_readings_non_binding(r, product, direction):
    assert normalize._direction(product, _mode(r, "unified")) == direction


def test_unified_gives_the_product_name_reader_terms_it_was_silent_about(r):
    """28 terms gain an opinion. Two examples, one each way, so the direction of the
    gain is pinned rather than just its existence."""
    assert normalize._direction("LamG domain-containing protein",
                                _mode(r, "current")) == "uninformative"
    assert normalize._direction("LamG domain-containing protein",
                                _mode(r, "unified")) == _RB
    assert normalize._direction("collagen repeat protein",
                                _mode(r, "unified")) == _STRUCT


def test_unified_loses_the_helper_noun_distinction(r):
    """A known cost, recorded rather than hidden: `rbp_ontology` has no helper terms, so
    "tail fiber assembly protein" reads apparatus instead of structural_only. Both are
    non-binding, so a chaperone is still not promoted, but the role label changes.
    Adding helper terms to the ontology is a further vocabulary change and needs its own
    measurement."""
    assert normalize._direction("tail fiber assembly protein",
                                _mode(r, "current")) == _STRUCT
    assert normalize._direction("tail fiber assembly protein",
                                _mode(r, "unified")) == _APP


# ------------------------------------------------------------- the override is recorded

def test_an_override_is_recorded_so_a_number_is_attributable(r):
    assert r.overrides == {}
    r.set_override("vocabulary.mode", "unified")
    assert r.overrides == {"vocabulary.mode": "unified"}
    assert r.get("meta.version")          # unrelated keys still resolve


def test_the_run_records_which_vocabulary_produced_it(tmp_path):
    import json
    from rbpfinder import cli
    import test_end_to_end as e2e

    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    out = tmp_path / "run"
    assert cli.main(["--genbank", str(gbk), "--phage-id", "SYN", "--out", str(out),
                     "--no-local-stage2", "--quiet-progress",
                     "--vocabulary", "unified"]) == 0
    recorded = json.loads((out / "run_result.json").read_text())["vocabulary"]
    assert recorded == {"mode": "unified", "overridden": True}
