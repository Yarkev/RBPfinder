"""The two skill packages must stay truthful about what the tool can actually do.

There are two packages -- `packaging/skill/` for desktop/Windows and
`packaging/biomni/skill/` for Linux servers -- and they drifted: commit a774bd1 edited the
desktop SKILL.md while standardising the *server* route, so for a while the desktop
package said all three of

  * "Windows is validated. Linux and macOS are not yet validated"
  * here is the EBI authorization protocol, walk the user through it
  * "not available in this build. This Linux/Biomni build refuses --allow-remote"

at the same time. An agent reading that would have offered a user a flag that exits 2.

These tests pin the two properties that failure violated: a skill package must not present
a refused flag as a usable capability, and each package must say which one it is. They
read the docs as text on purpose -- that is how an agent reads them.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "packaging" / "skill" / "rbpfinder"
LINUX = ROOT / "packaging" / "biomni" / "skill" / "rbpfinder"
PACKAGES = [DESKTOP, LINUX]

# Flags main() refuses with exit 2, before any ingest. Kept as the single source for the
# docs test and asserted against the CLI below, so this list cannot quietly go stale.
REFUSED = ["--allow-remote", "--data-policy remote_allowed", "--stage2-backend ebi"]


def _docs(pkg):
    return sorted(pkg.rglob("*.md"))


def _ids(paths):
    return [str(p.relative_to(ROOT)) for p in paths]


@pytest.fixture(scope="module")
def cli_source():
    return (ROOT / "rbpfinder" / "cli.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------ the refusals are real

def test_every_refused_flag_is_actually_refused_in_the_cli(cli_source):
    """If a flag stops being refused, the docs below are wrong in the other direction."""
    block = cli_source[cli_source.index("Remote submission is REMOVED"):]
    block = block[:block.index("Data policy: decided ONCE")]
    for flag in REFUSED:
        token = flag.split()[-1] if " " in flag else flag
        assert token in block, flag
    assert block.count("raise InputValidationError") == len(REFUSED)


def test_the_refusal_does_not_claim_to_be_platform_specific(cli_source):
    """It is unconditional, so a desktop user must not be told it is a Linux build's
    doing -- they would reasonably go looking for a build that does allow it."""
    assert "disabled in this build (linux/biomni)" not in cli_source
    assert cli_source.count('"remote submission is disabled in RBPfinder: "') == len(REFUSED)


def test_the_help_text_does_not_advertise_ebi_as_a_capability(cli_source):
    """`ebi` stays a parseable choice so an old command line gets the explicit refusal
    rather than argparse's "invalid choice" -- but the help must say it is removed."""
    i = cli_source.index('p.add_argument("--stage2-backend"')
    help_text = cli_source[i:i + 900]
    assert "REMOVED" in help_text
    assert "requires remote_allowed plus per-run" not in help_text


# ------------------------------------------------------- the docs do not offer a refusal

@pytest.mark.parametrize("doc", _docs(DESKTOP) + _docs(LINUX),
                         ids=_ids(_docs(DESKTOP) + _docs(LINUX)))
def test_no_skill_doc_shows_a_runnable_command_using_a_refused_flag(doc):
    """Mentioning a refused flag is required -- an agent has to know not to offer it.
    Putting it in a command line is the defect: that is what gets copied and run."""
    for line in doc.read_text(encoding="utf-8").splitlines():
        if "rbpfinder" not in line:
            continue
        if line.lstrip().startswith((">", "#", "*", "-", "|")):
            continue        # prose and tables discuss the flags; that is the point
        for flag in REFUSED:
            token = flag.split()[-1] if " " in flag else flag
            assert token not in line, "runnable %s in %s: %s" % (flag, doc.name, line)


@pytest.mark.parametrize("doc", _docs(DESKTOP), ids=_ids(_docs(DESKTOP)))
def test_the_desktop_package_never_asks_for_an_ebi_email(doc):
    """The whole submission flow is gone, so a contact address has no purpose. Asking for
    one would also be the single most user-visible symptom of this drift returning."""
    text = doc.read_text(encoding="utf-8")
    assert "--ebi-email" not in text
    for phrase in ("will be transmitted", "RBPfinder will retrieve and parse"):
        assert phrase not in text, "%s still describes a submission: %r" % (doc.name, phrase)


# ------------------------------------------------------------ each package says which it is

def test_the_desktop_package_identifies_itself_and_does_not_claim_to_be_the_linux_build():
    text = (DESKTOP / "SKILL.md").read_text(encoding="utf-8")
    assert "desktop / Windows" in text
    assert "This Linux/Biomni build" not in text
    # the stale validation claim that started this
    assert "Linux and macOS are not yet validated" not in text


def test_the_linux_package_identifies_itself():
    text = (LINUX / "SKILL.md").read_text(encoding="utf-8")
    assert "linux/biomni build" in text.lower()


def test_each_package_points_at_the_other():
    """An agent that lands in the wrong package should be told so, from either side. The
    difference is not cosmetic -- it changes which evidence families are reachable."""
    desktop = (DESKTOP / "SKILL.md").read_text(encoding="utf-8")
    linux = (LINUX / "SKILL.md").read_text(encoding="utf-8")
    assert "packaging/biomni/skill/rbpfinder/" in desktop
    assert "packaging/skill/rbpfinder/" in linux
    for text in (desktop, linux):
        assert "not interchangeable" in text


def test_neither_package_sends_the_user_to_a_build_that_allows_remote():
    """The refusal is unconditional in the source. A doc that frames it as this build's
    doing invites "then use the other build", and there is no other build."""
    for pkg in PACKAGES:
        for doc in _docs(pkg):
            text = doc.read_text(encoding="utf-8")
            assert "This build has no remote path" not in text, doc.name
            assert "not available in this build" not in text, doc.name


def test_the_desktop_package_states_the_family_T_platform_wall():
    """A Windows run cannot produce family T, and a missing family T must not be allowed
    to read as "no structural support" when it means "not looked for on this machine"."""
    text = (DESKTOP / "SKILL.md").read_text(encoding="utf-8")
    assert "no native Windows build" in text
    assert re.search(r"not looked for on this machine", text)


# ------------------------------------------------------------------- structural integrity

@pytest.mark.parametrize("pkg", PACKAGES, ids=["desktop", "linux"])
def test_every_referenced_reference_file_exists(pkg):
    text = (pkg / "SKILL.md").read_text(encoding="utf-8")
    for ref in sorted(set(re.findall(r"references/[A-Za-z0-9_]+\.md", text))):
        assert (pkg / ref).is_file(), "%s references a missing %s" % (pkg.name, ref)


@pytest.mark.parametrize("pkg", PACKAGES, ids=["desktop", "linux"])
def test_frontmatter_has_a_name_and_a_description(pkg):
    lines = (pkg / "SKILL.md").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    end = lines.index("---", 1)
    keys = [l.split(":", 1)[0] for l in lines[1:end] if ":" in l]
    assert "name" in keys and "description" in keys
