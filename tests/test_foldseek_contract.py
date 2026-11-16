"""Regressions for the defects a real foldseek binary exposed (2026-11-16).

The local structure route had been validated only against a fake binary, which emitted
whatever columns it was asked for. That proved the plumbing and could not prove the
CONTRACT. Running the real thing against the official PDB database found four defects,
each pinned below.
"""
import json
import pathlib
import re

import pytest

from rbpfinder import schema as schema_mod
from rbpfinder import stage3_local
from rbpfinder.audit import completeness
from rbpfinder.evidence import structural
from rbpfinder.ingest import genbank as genbank_in
from rbpfinder.providers.local_foldseek import LocalFoldseek, StructureDatabase
from rbpfinder.validate_cli import inspect_genbank

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _make_db(root, label="pdb100", taxonomy=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / (label + ".dbtype")).write_text("x")
    if taxonomy:
        for suffix in stage3_local.TAXONOMY_SUFFIXES:
            (root / (label + suffix)).write_text("x")
    return root / label


# ---------------------------------------------------------------- P1: the `prob` column

# A ProstT5 query is a sequence, so it has no backbone coordinates. Asking foldseek for
# any score that needs them kills easy-search:
#   No datafile could be found for .../query_ca! Error: Convert Alignments died
# (steineggerlab/foldseek issue #305). The contract used to request `prob`, which made
# the entire local structure route unusable against a real binary.
_COORDINATE_ONLY_COLUMNS = {"prob", "lddt", "lddtfull", "alntmscore", "qtmscore",
                            "ttmscore", "rmsd", "u", "t"}


def test_column_contract_requests_nothing_needing_backbone_coordinates(rules):
    cols = {str(c) for c in rules.get("stage3.local_foldseek.format_output")}
    offending = cols & _COORDINATE_ONLY_COLUMNS
    assert not offending, (
        "these columns need query backbone coordinates, which a ProstT5 query does not "
        "have; easy-search dies on query_ca: %s" % sorted(offending))


def test_executor_fallback_columns_match_the_contract(rules, tmp_path):
    """The hardcoded fallback must not reintroduce what the contract removed."""
    db = StructureDatabase("pdb100", str(_make_db(tmp_path / "db")))
    exe = tmp_path / "foldseek"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    with_rules = LocalFoldseek(databases=[db], prostt5_model=None,
                               foldseek_exe=str(exe), rules=rules)
    without_rules = LocalFoldseek(databases=[db], prostt5_model=None,
                                  foldseek_exe=str(exe), rules=None)
    assert with_rules.columns == without_rules.columns
    assert not set(without_rules.columns) & _COORDINATE_ONLY_COLUMNS


# ------------------------------------------------- P2: a hit with no probability is legal

def test_schema_accepts_a_structural_record_without_a_probability(rules):
    """`probability_pct: null` is the expected state on the ProstT5 route, not a fault."""
    schema = schema_mod.load(ROOT / "rules" / "evidence_schema.json")
    node = schema["$defs"] if "$defs" in schema else schema.get("definitions", {})
    # locate the probability_pct declaration wherever it lives and check it admits null
    text = json.dumps(schema)
    assert '"probability_pct"' in text
    idx = text.index('"probability_pct"')
    window = text[idx:idx + 400]
    assert '"null"' in window, "probability_pct must admit null: %s" % window[:200]
    assert node is not None


def test_consensus_keeps_a_hit_whose_probability_is_absent(rules):
    """Dropping the column is a safe degradation only because the hit is still kept."""
    rec = {"cds_id": "X_CDS_0001", "length_aa": 300}
    hits = [{"rank": 1, "database": "pdb100", "target": "7ung-assembly1_o1",
             "description": "Crystal structure of a tail fiber protein",
             "probability": None, "evalue": 1e-20, "score": 300.0,
             "seq_identity": 0.4, "query_span": (3, 290), "target_span": (1, 288),
             "query_length": 300, "taxon": "Escherichia phage"}]
    t_rec, cons = structural.build(rec, hits, rules, "local_foldseek")
    assert cons["n_considered"] == 1
    assert t_rec["family"] == "T"
    assert t_rec["raw"]["probability_pct"] is None


# --------------------------------- P3a: A2 can see a genome-wide local structure search

def test_a2_probe_accepts_genome_wide_local_structure_hits(rules):
    """The probe read only `tophit_protein`, which only a phold TSV import ever fills.

    A run that searched all 60 CDS with local Foldseek and found 48 hits still reported
    A2 unexecutable, because local results never reach that field.
    """
    cds = [{"cds_id": "T_CDS_%04d" % i, "tophit_protein": None} for i in range(1, 5)]
    ok, _detail = completeness.genome_wide_T_or_M_available(cds, rules)
    assert ok is False

    ok2, detail2 = completeness.genome_wide_T_or_M_available(
        cds, rules, structural_tophits={"T_CDS_0002": "7ung-assembly1_o1"})
    assert ok2 is True
    assert "local structure-search" in detail2


def test_a2_probe_still_refuses_sequence_derived_targets(rules):
    """The whitelist must not weaken: a sequence hit may not become T."""
    cds = [{"cds_id": "T_CDS_0001", "tophit_protein": None}]
    ok, _ = completeness.genome_wide_T_or_M_available(
        cds, rules, structural_tophits={"T_CDS_0001": "WP_000123456"})
    assert ok is False


# ------------------------------------- P3b: the official PDB id shape must be whitelisted

@pytest.mark.parametrize("target", [
    "7ung-assembly1_o1", "8dsp-assembly1_E", "4a0t-assembly1_C", "9jyz-assembly1_B",
])
def test_official_pdb_assembly_ids_are_recognised_as_structure_hits(target, rules):
    pattern = rules.get("evidence_families.structure_database_tophit_pattern")
    assert re.match(pattern, target), (
        "the official `foldseek databases PDB` download emits this shape; it used to "
        "fail the whitelist because an underscore was required after the 4-char id")


@pytest.mark.parametrize("target", [
    "WP_123456", "envhog_abc", "protein12", "acr_xyz", "vfdb_1",
])
def test_sequence_derived_namespaces_still_fail_the_whitelist(target, rules):
    """Widening the pattern must not let a sequence datum be counted twice (D and T)."""
    pattern = rules.get("evidence_families.structure_database_tophit_pattern")
    assert not re.match(pattern, target)


def test_stage3_local_reports_the_best_target_per_cds():
    doc = {"results": [{"db": "pdb100", "alignments": [[
        {"target": "7ung-assembly1_o1 Crystal structure of something"},
        {"target": "1abc_A Another hit"}]]}]}
    assert stage3_local._best_target(doc) == "7ung-assembly1_o1"
    assert stage3_local._best_target({"results": []}) is None


# ------------------------------------------------- P4: an undeclared topology is reported

def _genbank(path, topology=None):
    from Bio import SeqIO
    from Bio.Seq import Seq
    from Bio.SeqFeature import FeatureLocation, SeqFeature
    from Bio.SeqRecord import SeqRecord
    genome, feats = "", []
    for i in range(3):
        nt = "ATG" + "GCT" * 99 + "TAA"
        start = len(genome)
        genome += nt + "ACGT" * 10
        feats.append(SeqFeature(
            FeatureLocation(start, start + len(nt), strand=1), type="CDS",
            qualifiers={"locus_tag": ["G_CDS_%04d" % i],
                        "product": ["tail fiber protein"],
                        "translation": ["M" + "A" * 99]}))
    ann = {"molecule_type": "DNA"}
    if topology:
        ann["topology"] = topology
    rec = SeqRecord(Seq(genome), id="G", name="G", description="g", annotations=ann)
    rec.features = feats
    SeqIO.write(rec, str(path), "genbank")
    return path


def test_validate_input_warns_when_no_topology_is_declared(tmp_path):
    """Silent degradation: an undeclared circular genome loses C1/C3 nominations across
    the origin while the POOL SIZE stays the same, so no printed number reveals it."""
    p = _genbank(tmp_path / "no_topo.gbk", topology=None)
    info = inspect_genbank(p, phage_id="G")
    assert info["usable"] is True
    assert info["declares_topology"] is False
    assert any("no topology declared" in w for w in info["warnings"])


@pytest.mark.parametrize("topology", ["linear", "circular"])
def test_validate_input_is_quiet_when_topology_is_declared(tmp_path, topology):
    p = _genbank(tmp_path / ("t_%s.gbk" % topology), topology=topology)
    info = inspect_genbank(p, phage_id="G")
    assert info["declares_topology"] is True
    assert not any("no topology declared" in w for w in info["warnings"])
    assert info["is_circular"] is (topology == "circular")


def test_genbank_meta_reports_whether_topology_was_declared(tmp_path):
    _cds, meta = genbank_in.read(_genbank(tmp_path / "a.gbk", "circular"), phage_id="G")
    assert meta["declares_topology"] is True
    _cds2, meta2 = genbank_in.read(_genbank(tmp_path / "b.gbk", None), phage_id="G")
    assert meta2["declares_topology"] is False
    assert meta2["is_circular"] is False


# ---------------------------------------- P5: a database without taxonomy is not usable

def test_structure_database_without_taxonomy_files_is_refused(rules, tmp_path):
    """doctor reported AVAILABLE for a database that killed every search at run time."""
    prefix = _make_db(tmp_path / "mini", "pdb100", taxonomy=False)
    use, decisions = stage3_local.resolve_structure_databases(
        rules, explicit=[("pdb100", str(prefix))])
    assert use == []
    assert decisions[0]["reason"] == "missing_taxonomy"
    assert decisions[0]["fatal"] is True
    assert "foldseek databases" in decisions[0]["detail"]


def test_structure_database_with_taxonomy_files_is_accepted(rules, tmp_path):
    prefix = _make_db(tmp_path / "full", "pdb100", taxonomy=True)
    use, _decisions = stage3_local.resolve_structure_databases(
        rules, explicit=[("pdb100", str(prefix))])
    assert [d.label for d in use] == ["pdb100"]


def test_taxonomy_is_only_required_when_the_contract_asks_for_it(rules):
    """The check is tied to the column contract, not hardcoded."""
    assert stage3_local.needs_taxonomy(rules) is True

    class _NoTaxname:
        def get(self, key, default=None):
            if key == "stage3.local_foldseek.format_output":
                return ["query", "target", "theader", "evalue"]
            return rules.get(key, default)

    assert stage3_local.needs_taxonomy(_NoTaxname()) is False


# ------------------------ the official PDB theader already carries the identifier

def _aln(rules, target, theader):
    db = StructureDatabase("pdb100", "x")
    lf = LocalFoldseek.__new__(LocalFoldseek)
    lf.columns = list(rules.get("stage3.local_foldseek.format_output"))
    lf.databases = [db]
    cols = [target, theader, "1e-20", "900", "1.0", "4", "166", "1", "163", "553", "tax"]
    return lf._row_to_alignment(["QUERY"] + cols)[1]


def test_identifier_is_not_duplicated_into_the_description(rules):
    """Real data: `theader` from the official PDB database starts with the identifier."""
    aln = _aln(rules, "8dsp-assembly1_E",
               "8dsp-assembly1_E Cryo-EM structure of the proximal half fiber")
    assert aln["target"] == "8dsp-assembly1_E Cryo-EM structure of the proximal half fiber"
    assert aln["target"].count("8dsp-assembly1_E") == 1


def test_identifier_is_still_prepended_when_the_header_lacks_it(rules):
    aln = _aln(rules, "1abc_A", "Some crystal structure")
    assert aln["target"] == "1abc_A Some crystal structure"


# ------------- taxonomy comes in two shapes; requiring only one blocked the real thing

def test_official_single_file_taxonomy_is_accepted(rules, tmp_path):
    """`foldseek databases` ships ONE <prefix>_taxonomy file, not the .dmp set.

    The first version of this check demanded the four-file set on the strength of a
    comment asserting the official download shipped it. It does not. The official PDB
    database was then refused with exit 2 before any search could run -- a worse failure
    than the false positive the check was added to remove.
    """
    root = tmp_path / "official"
    root.mkdir()
    (root / "pdb100.dbtype").write_text("x")
    (root / ("pdb100" + stage3_local.TAXONOMY_SINGLE_SUFFIX)).write_text("x")
    assert stage3_local.taxonomy_missing(root / "pdb100") == []
    use, _ = stage3_local.resolve_structure_databases(
        rules, explicit=[("pdb100", str(root / "pdb100"))])
    assert [d.label for d in use] == ["pdb100"]


def test_dmp_file_set_is_still_accepted(rules, tmp_path):
    prefix = _make_db(tmp_path / "dmp", "pdb100", taxonomy=True)
    assert stage3_local.taxonomy_missing(prefix) == []
    use, _ = stage3_local.resolve_structure_databases(
        rules, explicit=[("pdb100", str(prefix))])
    assert [d.label for d in use] == ["pdb100"]


def test_a_database_with_neither_shape_is_still_refused(rules, tmp_path):
    prefix = _make_db(tmp_path / "none", "pdb100", taxonomy=False)
    assert stage3_local.taxonomy_missing(prefix)
    use, decisions = stage3_local.resolve_structure_databases(
        rules, explicit=[("pdb100", str(prefix))])
    assert use == []
    assert decisions[0]["reason"] == "missing_taxonomy"


# ------------------------------------- A2's scan body, which the probe gates

def test_a2_scan_flags_out_of_pool_cds_with_a_structural_hit(rules):
    """The scattered-RBP miss C1 and C4 structurally cannot see."""
    cds = [{"cds_id": "T_CDS_%04d" % i, "product": "hypothetical protein",
            "function": "unknown function", "tophit_protein": None}
           for i in range(1, 5)]
    pool = {"T_CDS_0001"}
    hits = completeness.a2_context_negative_signal_positive(
        cds, pool, rules,
        structural_tophits={"T_CDS_0001": "7ung-assembly1_o1",     # in pool: skip
                            "T_CDS_0003": "8dsp-assembly1_E",      # flag
                            "T_CDS_0004": "WP_000123"})            # not a structure hit
    assert [h["cds_id"] for h in hits] == ["T_CDS_0003"]
    assert hits[0]["audit_id"] == "A2"
    assert hits[0]["reason_class"] == rules.get("stage6.reason_classes.A2")


def test_a2_no_longer_raises_when_the_probe_says_yes(rules):
    """A NotImplementedError guard stood here so that enabling the probe could not
    silently report A2 as passed. Enabling it without the scan turned an honest
    'unexecutable' into a crash."""
    cds = [{"cds_id": "T_CDS_0001", "product": "tail fiber protein",
            "function": "tail", "tophit_protein": None,
            "start_nt": 1, "end_nt": 300, "length_aa": 100},
           {"cds_id": "T_CDS_0002", "product": "hypothetical protein",
            "function": "unknown function", "tophit_protein": None,
            "start_nt": 400, "end_nt": 700, "length_aa": 100}]
    hits, exec_state = completeness.run_cycle(
        cds, {"T_CDS_0001"}, [], rules, is_circular=False,
        structural_tophits={"T_CDS_0002": "8dsp-assembly1_E"})
    assert exec_state["A2"]["executable"] is True
    assert any(h["audit_id"] == "A2" and h["cds_id"] == "T_CDS_0002" for h in hits)
