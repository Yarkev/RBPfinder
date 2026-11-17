"""Regression tests for the fixes that came out of the 2026-11 review.

Written against the REAL `decision_rules.yaml` (the `rules` fixture), not a stub: the
defects these cover were all cases of code disagreeing with the rules file, so a stub
would keep passing exactly when it matters.
"""
import copy

import pytest
from Bio import SeqIO
from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord

from rbpfinder import cli
from rbpfinder.classify import evidence_families, tiers
from rbpfinder.ingest import genbank as genbank_in
from rbpfinder.nominate import c1_context


# --------------------------------------------------------------------------- helpers

def _rows(products, nt=1000):
    return [{"cds_id": "P_CDS_%04d" % i, "product": p,
             "start_nt": i * nt + 1, "end_nt": (i + 1) * nt}
            for i, p in enumerate(products)]


def _genbank(path, products, topology="linear", tags=None):
    feats, genome = [], ""
    for i, prod in enumerate(products):
        prot = "M" + "A" * 99
        nt = "ATG" + "GCT" * 99 + "TAA"
        start = len(genome)
        genome += nt + "ACGT" * 25
        tag = tags[i] if tags else "G_CDS_%04d" % i
        feats.append(SeqFeature(
            FeatureLocation(start, start + len(nt), strand=1), type="CDS",
            qualifiers={"locus_tag": [tag], "product": [prod],
                        "translation": [prot], "function": ["unknown function"]}))
    rec = SeqRecord(Seq(genome), id="T", name="T", description="t",
                    annotations={"molecule_type": "DNA", "topology": topology})
    rec.features = feats
    SeqIO.write(rec, str(path), "genbank")
    return path


# ------------------------------------------------- B1: C1 circular adjacency

# Tail anchors only at the very start; the CDS immediately BEFORE the origin are
# unannotated. Linearly they are unreachable; on the circle they are adjacent.
_ORIGIN_SPANNING = (["tail fiber protein", "tail spike protein"]
                    + ["hypothetical protein"] * 6
                    + ["DNA polymerase", "terminase large subunit"]
                    + ["hypothetical protein"] * 2
                    + ["portal protein"]
                    + ["hypothetical protein"] * 7)


def test_circular_window_crosses_the_origin(rules):
    rows = _rows(_ORIGIN_SPANNING)
    linear = c1_context.run(rows, rules, is_circular=False)
    circular = c1_context.run(rows, rules, is_circular=True)

    # the origin-adjacent CDS are reachable only on the circle
    assert "P_CDS_0019" not in linear["nominated"]
    assert "P_CDS_0019" in circular["nominated"]
    assert len(circular["nominated"]) > len(linear["nominated"])


def test_circular_tail_module_split_by_origin_forms_one_window(rules):
    # anchors on BOTH sides of the origin: linearly two clusters, circularly one
    products = (["tail fiber protein"] + ["hypothetical protein"] * 17
                + ["baseplate protein", "tail spike protein"])
    rows = _rows(products)
    assert len(c1_context.run(rows, rules, is_circular=False)["windows"]) == 2
    assert len(c1_context.run(rows, rules, is_circular=True)["windows"]) == 1


def test_linear_path_is_unchanged_by_the_circular_fix(rules):
    """The frozen linear behaviour must not move; only circular genomes change."""
    rows = _rows(_ORIGIN_SPANNING)
    out = c1_context.run(rows, rules, is_circular=False)
    # anchors at 0,1 (both strong) -> hi = 1 + 5 = 6; the 3 kb extension reaches no
    # further at 1 kb per CDS, and the boundary at index 8 is outside the window anyway
    assert out["nominated"] == {"P_CDS_%04d" % i for i in range(0, 7)}
    assert out["clusters"] == 1


def test_boundary_anchor_still_truncates_a_circular_window(rules):
    rows = _rows(_ORIGIN_SPANNING)
    nominated = c1_context.run(rows, rules, is_circular=True)["nominated"]
    # the replication boundary must still stop the window
    assert "P_CDS_0008" not in nominated      # DNA polymerase
    assert "P_CDS_0009" not in nominated      # terminase


def test_no_anchor_still_abandons_the_channel_when_circular(rules):
    rows = _rows(["hypothetical protein"] * 10)
    out = c1_context.run(rows, rules, is_circular=True)
    assert out["nominated"] == set()
    assert out["warning"]


# ------------------------------- B1b: the resolved circularity must reach the channel

def test_genbank_topology_resolves_circularity_without_the_flag(tmp_path, rules):
    """`topology: circular` must be honoured even when --circular was not passed.

    The CLI used to pass `args.circular` (False by default) as the override, which
    suppressed the reader's own topology detection and left the circular fix dormant on
    exactly the records it was written for.
    """
    p = _genbank(tmp_path / "c.gbk", _ORIGIN_SPANNING, topology="circular")
    _cds, meta = genbank_in.read(p, phage_id="T")
    assert meta["is_circular"] is True

    lin = _genbank(tmp_path / "l.gbk", _ORIGIN_SPANNING, topology="linear")
    _cds2, meta2 = genbank_in.read(lin, phage_id="T")
    assert meta2["is_circular"] is False


def test_explicit_flag_still_forces_circular(tmp_path):
    p = _genbank(tmp_path / "l.gbk", _ORIGIN_SPANNING, topology="linear")
    _cds, meta = genbank_in.read(p, phage_id="T", is_circular=True)
    assert meta["is_circular"] is True


# ------------------------------------------------------- B2: non-pharokka locus tags

def test_ncbi_style_locus_tags_are_accepted(tmp_path):
    p = _genbank(tmp_path / "t7.gbk", ["tail fiber protein", "portal protein"],
                 tags=["T7p17", "T7p08"])
    cds, meta = genbank_in.read(p, phage_id="T7")
    assert [c["cds_id"] for c in cds] == ["T7_CDS_0001", "T7_CDS_0002"]
    # the raw tag is what makes the assignment traceable
    assert [c["raw_locus_tag"] for c in cds] == ["T7p17", "T7p08"]
    assert len(meta["warnings"]) == 2
    assert "coordinate order" in meta["warnings"][0]


def test_fallback_numbering_follows_coordinates_not_file_order(tmp_path):
    """Numbers come from the genome, not from the order features happen to be written.

    File order is a property of the file; a re-export with features reordered would
    otherwise renumber every protein, and `cds_id` is the key every artefact, manifest
    and replay is written against.
    """
    genome = ""
    feats = []
    coords = []
    for i in range(3):
        nt = "ATG" + "GCT" * 99 + "TAA"
        start = len(genome)
        coords.append(start)
        genome += nt + "ACGT" * 10
    # write the features in REVERSE coordinate order on purpose
    for rank, start in reversed(list(enumerate(coords))):
        feats.append(SeqFeature(
            FeatureLocation(start, start + 303, strand=1), type="CDS",
            qualifiers={"locus_tag": ["X%d" % rank], "product": ["hypothetical protein"],
                        "translation": ["M" + "A" * 99]}))
    rec = SeqRecord(Seq(genome), id="R", name="R", description="r",
                    annotations={"molecule_type": "DNA", "topology": "linear"})
    rec.features = feats
    p = tmp_path / "rev.gbk"
    SeqIO.write(rec, str(p), "genbank")

    cds, _meta = genbank_in.read(p, phage_id="R")
    # first by coordinate is X0 and must carry the first number
    assert [(c["cds_id"], c["raw_locus_tag"]) for c in cds] == [
        ("R_CDS_0001", "X0"), ("R_CDS_0002", "X1"), ("R_CDS_0003", "X2")]


def test_explicit_cds_numbers_still_win_and_do_not_collide(tmp_path):
    p = _genbank(tmp_path / "m.gbk", ["tail fiber protein", "portal protein"],
                 tags=["A_CDS_0007", "weird_tag"])
    cds, _meta = genbank_in.read(p, phage_id="M")
    ids = {c["raw_locus_tag"]: c["cds_id"] for c in cds}
    assert ids["A_CDS_0007"] == "M_CDS_0007"       # explicit number preserved
    assert ids["weird_tag"] == "M_CDS_0008"        # assigned above the highest explicit


def test_duplicate_explicit_numbers_remain_a_hard_failure(tmp_path):
    p = _genbank(tmp_path / "d.gbk", ["tail fiber protein", "portal protein"],
                 tags=["A_CDS_0003", "B_CDS_0003"])
    with pytest.raises(genbank_in.GenBankIngestError):
        genbank_in.read(p, phage_id="D")


def test_a_cds_with_no_identifier_remains_a_hard_failure(tmp_path):
    genome = "ATG" + "GCT" * 99 + "TAA"
    rec = SeqRecord(Seq(genome), id="N", name="N", description="n",
                    annotations={"molecule_type": "DNA", "topology": "linear"})
    rec.features = [SeqFeature(FeatureLocation(0, 303, strand=1), type="CDS",
                               qualifiers={"product": ["tail fiber protein"],
                                           "translation": ["M" + "A" * 99]})]
    p = tmp_path / "n.gbk"
    SeqIO.write(rec, str(p), "genbank")
    with pytest.raises(genbank_in.GenBankIngestError):
        genbank_in.read(p, phage_id="N")


# --------------------------------------------------- the exit-code contract (errors.py)

def test_ingest_error_is_an_input_error_not_an_internal_one(tmp_path, capsys):
    """SKILL.md tells agents 2 = input error, 1 = unexpected internal error.

    GenBankIngestError used to derive from bare Exception, so a malformed input file
    escaped as a traceback with exit 1 and an agent read it as a tool defect.
    """
    p = _genbank(tmp_path / "d.gbk", ["tail fiber protein", "portal protein"],
                 tags=["A_CDS_0003", "B_CDS_0003"])
    code = cli.main(["--genbank", str(p), "--phage-id", "D",
                     "--out", str(tmp_path / "o"), "--no-local-stage2",
                     "--quiet-progress"])
    assert code == 2
    assert "Input error:" in capsys.readouterr().err


def test_a_missing_input_file_is_an_input_error(tmp_path, capsys):
    code = cli.main(["--genbank", str(tmp_path / "nope.gbk"), "--phage-id", "X",
                     "--out", str(tmp_path / "o"), "--no-local-stage2",
                     "--quiet-progress"])
    assert code == 2
    assert "does not exist" in capsys.readouterr().err


# ------------------------------------------------------------- remote build disable

@pytest.mark.parametrize("extra", [
    ["--allow-remote"],
    ["--data-policy", "remote_allowed"],
    ["--stage2-backend", "ebi"],
])
def test_remote_submission_is_refused_by_this_build(tmp_path, capsys, extra):
    p = _genbank(tmp_path / "g.gbk", ["tail fiber protein"])
    out = tmp_path / "never"
    code = cli.main(["--genbank", str(p), "--phage-id", "G", "--out", str(out),
                     "--quiet-progress"] + extra)
    assert code == 2
    assert "remote submission is disabled" in capsys.readouterr().err
    assert not out.exists()          # refused before anything was written


# ------------------------------- representative selection must not hinge on tool names

def _rec(family, tool, direction, strength="strong"):
    return {"family": family, "tool": tool, "strength": strength,
            "direction": direction, "superseded_by": None,
            "region": {"start_aa": 1, "end_aa": 100, "source": "manual"},
            "raw": {"hit_description": "x"}}


def test_direction_tie_break_is_not_alphabetical(rules):
    """`dual_role_precedence`: a structural name may never veto a binding result.

    Both records are family D at equal strength. Sorting by tool name put `hhpred`
    (apparatus) ahead of `pharokka_phrog` (receptor binding), so the family reported
    apparatus and the candidate lost two tiers -- decided by the alphabet.
    """
    records = [_rec("D", "hhpred", "supports_apparatus_membership"),
               _rec("D", "pharokka_phrog", "supports_receptor_binding")]
    evidence_families.decorrelate(records, rules)
    rep = [r for r in records if r["counts_as_independent"]]
    assert len(rep) == 1
    assert rep[0]["direction"] == "supports_receptor_binding"


def test_a_positive_non_rbp_assignment_is_never_hidden(rules):
    """Tier N depends on a counted non-RBP record; it must not lose to a tie-break."""
    records = [_rec("D", "aaa_tool", "supports_receptor_binding"),
               _rec("D", "zzz_tool", "supports_non_rbp_identity")]
    evidence_families.decorrelate(records, rules)
    rep = [r for r in records if r["counts_as_independent"]][0]
    assert rep["direction"] == "supports_non_rbp_identity"


def test_strength_still_decides_before_direction(rules):
    """The tie-break only applies at equal strength; strength remains primary."""
    records = [_rec("D", "a_tool", "supports_apparatus_membership", "strong"),
               _rec("D", "b_tool", "supports_receptor_binding", "weak")]
    evidence_families.decorrelate(records, rules)
    rep = [r for r in records if r["counts_as_independent"]][0]
    assert rep["strength"] == "strong"


def test_tier_no_longer_depends_on_tool_name_ordering(rules):
    """The end-to-end consequence: same evidence, same tier, whatever the tools sort."""
    def tier_for(tool_name):
        recs = [_rec("G", "c3_dark_matter", "supports_apparatus_membership", "moderate"),
                _rec("D", tool_name, "supports_apparatus_membership"),
                _rec("D", "pharokka_phrog", "supports_receptor_binding"),
                _rec("S", "local_blast_db", "supports_apparatus_membership")]
        recs = copy.deepcopy(recs)
        ind, _ = evidence_families.decorrelate(recs, rules)
        return tiers.assign(recs, ind, rules)["tier_r"]

    assert tier_for("aaa_before") == tier_for("zzz_after")


# ------------------- invariants the rules file claims are enforced, now enforced

def test_add_only_invariant_is_asserted_not_merely_true(rules):
    """`invariants.nomination_is_add_only` declared an assertion that did not exist.

    The invariant held as a property of the implementation -- a set, and channels that
    only add -- but a property is not a contract: the first channel to grow a filter
    would have broken it silently while the rules file went on claiming a gate.
    """
    enforcement = rules.get("invariants.nomination_is_add_only.enforcement")
    assert "asserted per channel" in enforcement
    # the exception is named rather than left to be discovered
    assert "suppress-nomination" in rules.get(
        "invariants.nomination_is_add_only.exception")

    import inspect
    from rbpfinder import cli
    src = inspect.getsource(cli.run)
    assert "nomination_is_add_only violated" in src, (
        "the assertion the rules file describes is missing from cli.run")


def test_rules_file_carries_no_standing_authorisation(rules):
    """Authorisation is a run record, not a rule.

    `standing_authorisation: granted: true` sat in the rules file, read by nothing, where
    an agent could take it for a default grant -- and where it travelled to anyone who
    cloned the repository.
    """
    policy = rules.get("data_policy")
    assert "standing_authorisation" not in policy
    assert "per-run" in policy["removed_standing_authorisation"]


def test_database_registry_location_is_overridable(tmp_path, monkeypatch):
    """`RBPFINDER_CONFIG` has to work: on the Biomni sandbox the default path is not in
    the persistent snapshot, so a hibernate/resume loses the DB_PHAGE registration while
    the database files themselves survive. setup_biomni.sh points it inside /workspace
    for exactly that reason, which makes this override load-bearing rather than a
    convenience."""
    from rbpfinder import database
    target = tmp_path / "registry.yaml"
    monkeypatch.setenv("RBPFINDER_CONFIG", str(target))
    assert database.config_path() == target

    monkeypatch.delenv("RBPFINDER_CONFIG")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert database.config_path() == tmp_path / "xdg" / "rbpfinder" / "databases.yaml"


def test_setup_script_pins_the_registry_into_the_persistent_prefix():
    import pathlib as _pl
    script = (_pl.Path(__file__).resolve().parent.parent
              / "packaging" / "biomni" / "setup_biomni.sh").read_text()
    assert "RBPFINDER_CONFIG" in script, (
        "setup_biomni.sh must pin the database registry inside the persistent prefix; "
        "the default path is wiped by hibernation on that sandbox")
