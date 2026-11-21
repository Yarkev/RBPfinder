"""Mode B must not lose family T in silence.

phold's `phold_per_cds_predictions.tsv` has no translation column. `stage3_local.prepare`
filtered on `r.get("translation")`, so in Mode B every record was dropped and the run
reported `cds_searched: 0` with no warning anywhere -- while a structure database was
configured, i.e. while the user had asked for that search.

Found by a real test round on a Salmonella phage, where it cost two true candidates: a
985 aa fibre subunit and an Ig-domain protein, with nothing in the output to say family T
had been dropped for the whole genome.

Two zeros have to stay distinguishable, and these tests pin both:
  * no CDS carries a sequence            -> an INPUT problem, refuse and say how to fix it
  * no CDS is in the nominated pool      -> a legitimate zero, stay silent
"""
import csv
import json
import pathlib

import pytest

from rbpfinder import cli
from rbpfinder.errors import InputValidationError
from rbpfinder.ingest import genbank as genbank_in
from rbpfinder.ingest import phold as phold_in
from rbpfinder.ingest import phold_sequences as phold_seq

import test_end_to_end as e2e

_COLS = ["cds_id", "contig_id", "start", "end", "strand", "phrog", "function",
         "product", "annotation_method", "annotation_source", "annotation_confidence",
         "bitscore", "fident", "evalue", "qStart", "qEnd", "qLen", "qCov",
         "tophit_protein", "prostt5_confidence"]


def _tsv_from_genbank(gbk, tsv, shift=0):
    """A phold-shaped TSV for the same CDS -- no translation column, as phold's has none.

    `shift` moves every coordinate, to stand in for a TSV and a GenBank that came from
    different annotation runs.
    """
    rows, _meta = genbank_in.read(gbk, phage_id="SYN")
    with open(tsv, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLS, delimiter="\t")
        w.writeheader()
        for r in rows:
            w.writerow({
                "cds_id": r["cds_id"], "contig_id": "SYN1",
                "start": r["start_nt"] + shift, "end": r["end_nt"] + shift,
                "strand": "+", "phrog": r.get("phrog") or "No_PHROG",
                "function": r.get("function") or "unknown function",
                "product": r["product"], "annotation_method": "foldseek",
                "annotation_source": "phold", "annotation_confidence": "high",
                "bitscore": 120.0, "fident": 0.42, "evalue": 1e-20,
                "qStart": 1, "qEnd": r["length_aa"], "qLen": r["length_aa"],
                "qCov": 0.98, "tophit_protein": "phrog_%s" % (r.get("phrog") or "x"),
                "prostt5_confidence": 0.8,
            })
    return tsv


@pytest.fixture()
def inputs(tmp_path):
    gbk = tmp_path / "phold.gbk"
    e2e._genbank(gbk)
    tsv = _tsv_from_genbank(gbk, tmp_path / "phold_per_cds_predictions.tsv")
    return gbk, tsv


# ------------------------------------------------ the TSV genuinely has no sequences

def test_the_phold_tsv_reader_produces_no_translations(inputs):
    """Not a parser bug to fix -- the format has no such column. Pinned so that the
    refusal below is understood as the right response rather than a workaround."""
    _gbk, tsv = inputs
    rows = phold_in.read(tsv)
    assert rows
    assert all(not r.get("translation") for r in rows)


# --------------------------------------------- zero one: an input problem, now loud

def _make_db(root, label="pdb100"):
    root.mkdir(parents=True, exist_ok=True)
    (root / (label + ".dbtype")).write_text("x")
    for suffix in ("_mapping", "_nodes.dmp", "_names.dmp", "_merged.dmp"):
        (root / (label + suffix)).write_text("x")
    return root / label


def test_structure_search_refuses_when_nothing_carries_a_sequence(
        inputs, rules, tmp_path):
    from rbpfinder import stage3_local
    _gbk, tsv = inputs
    rows = phold_in.read(tsv)
    _make_db(tmp_path / "db")

    with pytest.raises(InputValidationError) as exc:
        stage3_local.prepare(
            rows, rules, None, tmp_path / "out",
            prostt5_model=str(tmp_path / "prostt5"),
            explicit_dbs=[("pdb100", str(tmp_path / "db" / "pdb100"))])
    msg = str(exc.value)
    assert "not one CDS carries a protein sequence" in msg
    # The message has to carry the way out, or it is just a different silence.
    assert "--genbank phold.gbk" in msg
    assert "--phold-genbank" in msg
    assert "--no-local-stage3" in msg


# ------------------------------- zero two: an empty pool stays a legitimate quiet zero

def test_an_empty_nominated_pool_is_not_an_error(rules, tmp_path):
    """The pool being empty is itself Stage 1's answer; structure search having nothing
    to do follows from it and must not be reported as a broken input.

    The sequence check runs before any binary is resolved, so reaching the capability
    error is what proves the input passed it -- that ordering is the point, since a
    refusal placed after capability resolution would never be seen on a machine without
    foldseek installed.
    """
    from rbpfinder import stage3_local
    from rbpfinder.capability import FoldseekCapability
    from rbpfinder.errors import CapabilityUnavailable

    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    rows, _meta = genbank_in.read(gbk, phage_id="SYN")
    _make_db(tmp_path / "db")
    with pytest.raises(CapabilityUnavailable):
        stage3_local.prepare(
            rows, rules, FoldseekCapability(), tmp_path / "out",
            prostt5_model=str(tmp_path / "prostt5"),
            explicit_dbs=[("pdb100", str(tmp_path / "db" / "pdb100"))],
            restrict_ids=set())


# ---------------------------------------------- the GenBank supplies what the TSV lacks

def test_attaching_sequences_makes_mode_b_searchable(inputs):
    gbk, tsv = inputs
    rows = phold_in.read(tsv)
    notes = phold_seq.attach(rows, gbk, phage_id="SYN")
    assert all(r.get("translation") for r in rows)
    assert notes == []


def test_phold_fields_survive_the_attachment(inputs):
    """The reason to pass both rather than only the GenBank: the TSV carries per-CDS
    fields the GenBank does not."""
    gbk, tsv = inputs
    rows = phold_in.read(tsv)
    phold_seq.attach(rows, gbk, phage_id="SYN")
    r = rows[0]
    assert r["prostt5_confidence"] == 0.8
    assert r["qcov"] == 0.98
    assert r["translation"]


def test_a_mismatched_genbank_is_refused_rather_than_misattached(inputs, tmp_path):
    """A sequence attached to the wrong CDS is worse than no family T: it is a confident
    wrong answer, and nothing downstream could tell."""
    gbk, _tsv = inputs
    shifted = _tsv_from_genbank(gbk, tmp_path / "shifted.tsv", shift=99991)
    rows = phold_in.read(shifted)
    for r in rows:                      # ids differ too, as two runs' would
        r["cds_id"] = r["cds_id"].replace("CDS_", "OTHER_")
    with pytest.raises(InputValidationError) as exc:
        phold_seq.attach(rows, gbk, phage_id="SYN")
    assert "different annotation runs" in str(exc.value)


def test_a_genbank_with_no_cds_is_refused(inputs, tmp_path):
    gbk, tsv = inputs
    empty = tmp_path / "empty.gbk"
    empty.write_text("LOCUS       EMPTY   10 bp    DNA     linear   UNK\n"
                     "ORIGIN\n        1 acgtacgtac\n//\n", encoding="utf-8")
    rows = phold_in.read(tsv)
    with pytest.raises(InputValidationError):
        phold_seq.attach(rows, empty, phage_id="SYN")


# --------------------------------------------------------------- the CLI wiring itself

def test_the_flag_only_applies_to_mode_b(inputs, tmp_path):
    """In Mode A the GenBank already carries the sequences, so accepting it there would
    silently ignore a file the user explicitly passed."""
    gbk, _tsv = inputs
    rc = cli.main(["--genbank", str(gbk), "--phage-id", "SYN",
                   "--out", str(tmp_path / "o"), "--phold-genbank", str(gbk),
                   "--no-local-stage2", "--quiet-progress"])
    assert rc == 2


def test_mode_b_with_the_genbank_runs_and_matches_mode_a(inputs, tmp_path):
    """Acceptance criterion from the test round: the TSV route, given the sequences,
    must agree with the GenBank route on the candidate sets."""
    gbk, tsv = inputs
    a, b = tmp_path / "a", tmp_path / "b"
    assert cli.main(["--genbank", str(gbk), "--phage-id", "SYN", "--out", str(a),
                     "--no-local-stage2", "--quiet-progress"]) == 0
    assert cli.main(["--phold", str(tsv), "--phold-genbank", str(gbk),
                     "--phage-id", "SYN", "--out", str(b),
                     "--no-local-stage2", "--quiet-progress"]) == 0
    da = json.loads((a / "run_result.json").read_text())
    db = json.loads((b / "run_result.json").read_text())
    assert sorted(db["primary_set"]) == sorted(da["primary_set"])
    assert sorted(db["rescue_set"]) == sorted(da["rescue_set"])


def test_mode_b_without_the_genbank_still_runs_when_structure_is_off(inputs, tmp_path):
    """Refusing must be scoped to the case where structure was actually requested. A
    TSV-only run that wants no structure evidence was never broken."""
    _gbk, tsv = inputs
    assert cli.main(["--phold", str(tsv), "--phage-id", "SYN",
                     "--out", str(tmp_path / "c"), "--no-local-stage2",
                     "--no-local-stage3", "--quiet-progress"]) == 0


def test_the_help_explains_why_the_flag_exists():
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cli.main(["--help"])
    text = buf.getvalue()
    assert "--phold-genbank" in text
    assert "family T" in text
