"""A result must say which software and which rules produced it.

The rules file has claimed since v1 that "a run cites the rules version". It did not:
no version string appeared anywhere in `run_result.json`, so a benchmark number could not
be attributed to a release. The two version strings had also drifted apart -- the package
read `1.2.1+linux` and the rules read `1.2.1-linux`, close enough to look right in a diff,
and `-linux` is not a valid PEP 440 local version at all.

These tests make both failures impossible to reintroduce quietly.
"""
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject_version():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # The [project] table's own `version`, not a dependency pin further down.
    block = text.split("[project]", 1)[1].split("\n[", 1)[0]
    m = re.search(r'^version\s*=\s*"([^"]+)"', block, re.M)
    assert m, "pyproject [project] has no version"
    return m.group(1)


def _rules_version():
    text = (ROOT / "rules" / "decision_rules.yaml").read_text(encoding="utf-8")
    block = text.split("\nmeta:", 1)[1].split("\n\n", 1)[0]
    m = re.search(r'^\s+version:\s*"([^"]+)"', block, re.M)
    assert m, "decision_rules.yaml meta has no version"
    return m.group(1)


# ------------------------------------------------------------------ the two stay in step

def test_the_rules_version_is_byte_identical_to_the_package_version():
    """The rules file says they are "kept in step with the package version on purpose".
    Anything looser than byte-identical is what let +linux and -linux coexist."""
    assert _rules_version() == _pyproject_version()


def test_the_version_is_a_valid_pep440_local_version():
    """`1.2.1-linux` parses as a pre-release separator, not a local version, so it would
    normalise to something else entirely in distribution metadata."""
    v = _pyproject_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+(\+[a-z0-9]+(\.[a-z0-9]+)*)?", v), v
    assert "-" not in v


def test_nothing_hardcodes_a_third_copy_of_the_version():
    """A version written into the source would be a third place to keep in step. The
    package version is read from distribution metadata and the rules version from YAML."""
    for src in (ROOT / "rbpfinder").rglob("*.py"):
        text = src.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lstrip().startswith("#"):
                continue
            assert '"1.2.1' not in line and "'1.2.1" not in line, "%s: %s" % (src.name, line)


# --------------------------------------------------------------- a run cites both of them

@pytest.fixture()
def run_result(tmp_path):
    from rbpfinder import cli
    import test_end_to_end as e2e

    gbk = tmp_path / "syn.gbk"
    e2e._genbank(gbk)
    out = tmp_path / "run"
    assert cli.main(["--genbank", str(gbk), "--phage-id", "SYN", "--out", str(out),
                     "--no-local-stage2", "--quiet-progress"]) == 0
    return json.loads((out / "run_result.json").read_text(encoding="utf-8"))


def test_a_run_records_its_versions(run_result):
    v = run_result["versions"]
    assert set(v) == {"package", "rules", "schema"}
    assert v["rules"] == _rules_version()
    assert v["schema"].endswith(".json")


def test_the_recorded_package_version_is_honest_about_not_being_installed(run_result):
    """Read from distribution metadata, so a source tree that was never installed reports
    `0+unknown` rather than a number it cannot stand behind. Either is acceptable; a
    number that disagrees with the distribution is not."""
    import rbpfinder
    assert run_result["versions"]["package"] == rbpfinder.__version__


def test_the_version_block_survives_schema_validation(run_result):
    """It is added to a structure the CLI validates, so the field going in must not be
    the thing that makes a run exit 1. (The schema has no top-level properties and sets
    `additionalProperties` nowhere, so this holds -- the test exists to notice if that
    ever changes.)"""
    from rbpfinder import schema as schema_mod
    from rbpfinder import resources as _resources
    sch = schema_mod.load(_resources.default_schema_path())
    assert schema_mod.validate(run_result, sch) == []
