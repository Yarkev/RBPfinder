"""The bundle builder, and the boundary it enforces.

`packaging/README.md` documented a bundle layout and the installers hard-required it, but
nothing produced it: `scripts/build_release_bundle.py` was referenced by `.gitignore` and
did not exist, so the installers had never been executed and the README would have shipped
with `{{VERSION}}` / `{{WHEEL}}` still in it.

These tests cover the builder's own logic -- the classification boundary and the layout
contract -- without running a wheel build, which is slow and needs the network. Building
and installing for real is a release step, not a unit test.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "build_release_bundle.py"


@pytest.fixture(scope="module")
def builder():
    assert SCRIPT.is_file(), "the bundle builder is referenced by .gitignore and must exist"
    spec = importlib.util.spec_from_file_location("build_release_bundle", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------- internal material never ships

@pytest.mark.parametrize("rel", [
    "BIOMNI-HANDOFF.md",
    "CHANGES-linux.md",
    "HANDOFF.md",
    "MEMORY.md",
    "MILESTONE_7.md",
    "M12_PREREGISTRATION.md",
    "literature.xlsx",
    "packaging/benchmark/data.tsv",
    "packaging/comparison/compare_arms.py",
    "packaging/comparison/VOCABULARY-COMPARISON.md",
    "rbpfinder/__pycache__/cli.cpython-313.pyc",
])
def test_internal_material_is_refused(builder, rel):
    """The same boundary `.gitignore` draws, enforced by rule rather than by memory.
    The comparison harness is a maintainer tool: a user installs the analysis, not the
    instrument used to decide what the analysis should do."""
    assert builder.forbidden(rel), rel


@pytest.mark.parametrize("rel", [
    "packaging/README.md",
    "packaging/LICENSE",
    "packaging/install/install_linux.sh",
    "packaging/skill/rbpfinder/SKILL.md",
    "packaging/skill/rbpfinder/references/KNOWN_ISSUES.md",
    "packaging/biomni/skill/rbpfinder/SKILL.md",
    "packaging/agents/AGENTS.md",
])
def test_shippable_material_is_allowed(builder, rel):
    assert builder.forbidden(rel) is None, rel


# --------------------------------------------------------------- both skill packages ship

def test_the_bundle_ships_both_skill_packages(builder):
    """They are not interchangeable: family T is only reachable inside a run on Linux, so
    each package tells the agent a different thing about what a run can establish.
    install_linux.sh prefers skill/rbpfinder-linux; shipping only the desktop one would
    silently install the wrong skill on a server."""
    dests = dict((src, dst) for src, dst in builder.TREES)
    assert dests["packaging/skill"] == "skill"
    assert dests["packaging/biomni/skill/rbpfinder"] == "skill/rbpfinder-linux"


def test_the_linux_installer_prefers_the_linux_skill():
    sh = (ROOT / "packaging" / "install" / "install_linux.sh").read_text(encoding="utf-8")
    assert 'SRC="$ROOT/skill/rbpfinder-linux"' in sh
    assert '[ -d "$SRC" ] || SRC="$ROOT/skill/rbpfinder"' in sh


def test_the_builder_requires_the_linux_skill_in_its_own_verification(builder):
    """The installer falls back to the desktop skill when the Linux one is absent, which
    would be a silent wrong-skill install. The build must fail instead."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert '"skill/rbpfinder-linux/SKILL.md"' in src


# ------------------------------------------------------------------- the version contract

def test_the_builder_refuses_a_bundle_whose_two_versions_disagree(builder):
    """A run cites the rules version, so shipping a bundle whose package and rules numbers
    differ makes that citation meaningless. The builder fails rather than warning."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "rules_version" in src
    assert 'fail("the rules file says' in src
    assert builder.rules_version() is not None


def test_the_readme_placeholders_are_the_ones_the_builder_substitutes():
    """If the template gains a placeholder the builder does not know, the build fails on
    the leftover rather than shipping a README with {{...}} in it."""
    import re
    text = (ROOT / "packaging" / "README.md").read_text(encoding="utf-8")
    found = set(re.findall(r"\{\{[A-Z_]+\}\}", text))
    assert found <= {"{{VERSION}}", "{{WHEEL}}"}, found


def test_every_packaging_path_is_classified(builder):
    """The allowlist decides what ships, which is the right default. This catches the
    other failure: someone adds `packaging/newthing/`, it never reaches a user, and no
    build output mentions it. The module docstring used to CLAIM this check existed when
    it did not, so the claim was implemented rather than softened."""
    assert builder.unclassified_packaging_paths() == []


def test_the_check_catches_a_stray_path(builder, tmp_path, monkeypatch):
    """A gate that passes everything is worse than no gate."""
    stray = builder.ROOT / "packaging" / "_stray_test_only"
    stray.mkdir()
    try:
        (stray / "f.md").write_text("x", encoding="utf-8")
        found = builder.unclassified_packaging_paths()
        assert "packaging/_stray_test_only/f.md" in found
    finally:
        import shutil
        shutil.rmtree(stray)


def test_not_shipped_entries_carry_a_reason(builder):
    """A path excluded without a stated reason is indistinguishable from an oversight."""
    assert builder.NOT_SHIPPED
    for path, reason in builder.NOT_SHIPPED.items():
        assert len(reason) > 40, path


# ------------------------------------------------------- the Biomni skill package gate

BIOMNI_SKILL = ROOT / "packaging" / "biomni" / "skill" / "rbpfinder"


def test_the_shipped_biomni_skill_passes_its_own_gate(builder):
    """Run the gate here rather than only on the platform: a rejected upload costs a
    round trip and every failure mode is local."""
    assert builder.check_skill_package(BIOMNI_SKILL) == []


def test_the_gate_catches_a_stray_file(builder, tmp_path):
    """A file nobody references is either dead weight or a reference someone forgot to
    add. Both are worth stopping for, and a passing gate that catches nothing is worse
    than no gate."""
    import shutil
    pkg = tmp_path / "rbpfinder"
    shutil.copytree(BIOMNI_SKILL, pkg)
    (pkg / "references" / "ORPHAN.md").write_text("stray", encoding="utf-8")
    problems = builder.check_skill_package(pkg)
    assert any("never references it" in p for p in problems), problems


def test_the_gate_catches_a_missing_reference(builder, tmp_path):
    import shutil
    pkg = tmp_path / "rbpfinder"
    shutil.copytree(BIOMNI_SKILL, pkg)
    (pkg / "references" / "KNOW_HOW.md").unlink()
    problems = builder.check_skill_package(pkg)
    assert any("missing references/KNOW_HOW.md" in p for p in problems), problems


def test_the_gate_catches_a_machine_local_path(builder, tmp_path):
    """A path that resolves only on the machine that built the package is a dead
    instruction once it is deployed somewhere else."""
    import shutil
    pkg = tmp_path / "rbpfinder"
    shutil.copytree(BIOMNI_SKILL, pkg)
    sk = pkg / "SKILL.md"
    sk.write_text(sk.read_text(encoding="utf-8") + "\nSee /mnt/results/notes.md\n",
                  encoding="utf-8")
    problems = builder.check_skill_package(pkg)
    assert any("machine-local path" in p for p in problems), problems


def test_the_skill_tells_the_agent_to_cite_the_versions():
    """A tier or a rank only means something against the rules that produced it, and the
    run has recorded both since this release."""
    text = (BIOMNI_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "`versions`" in text
    assert "0+unknown" in text


# ------------------------------------------- the verifier checks the refusal, not the flag

def test_the_verifiers_check_the_refusal_rather_than_requiring_the_flag():
    """Both verifiers used to assert that --help DOCUMENTED the EBI email and
    --allow-remote. Remote submission has since been removed, so those checks required
    the install to advertise a capability that exits 2 -- a gate that would have failed
    the day someone correctly took the flags out."""
    for name in ("verify_install.sh", "verify_install.ps1"):
        text = (ROOT / "packaging" / "install" / name).read_text(encoding="utf-8")
        assert "documents the EBI contact email" not in text, name
        assert "documents --allow-remote" not in text, name
        assert "refused with exit 2" in text, name


def test_the_installers_do_not_promise_an_authorization_prompt():
    for name in ("install_linux.sh", "install_windows.ps1"):
        text = (ROOT / "packaging" / "install" / name).read_text(encoding="utf-8")
        assert "asks for authorization" not in text, name
