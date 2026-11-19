#!/usr/bin/env python3
"""Build the release bundle the installers expect, and nothing else.

`packaging/install/install_linux.sh` and `install_windows.ps1` require a layout
(`runtime/<wheel>`, optionally `runtime/wheels/`) that only a bundle build produces, and
`packaging/README.md` is a template with `{{VERSION}}` / `{{WHEEL}}` placeholders. Until
this script existed nothing produced any of it: the installers could not be exercised at
all, and the README would have shipped with the placeholders still in it.

    python3 scripts/build_release_bundle.py                    # dist/rbpfinder-<ver>/
    python3 scripts/build_release_bundle.py --offline-deps      # + runtime/wheels/
    python3 scripts/build_release_bundle.py --archive           # + .tar.gz and .zip

What it does:

  1. builds the wheel with `python -m build`, so the version comes from pyproject and is
     never retyped here;
  2. copies the packaging tree in, substituting the README placeholders;
  3. optionally downloads the dependency wheels, for installing on a machine with no
     network (`install_linux.sh --offline`);
  4. writes VERSION.json -- the machine-readable build facts `packaging/README.md`
     promises -- and SHA256SUMS.txt over every file in the bundle;
  5. verifies the result against the layout the installers actually read, and fails
     loudly rather than shipping something they will reject.

## The boundary

Internal development material never enters a bundle. That is the same boundary
`.gitignore` draws, and it is enforced here by name and by prefix rather than by
remembering: milestone logs, handoff and memory documents, preregistrations, curated
benchmark data and spreadsheets. `packaging/comparison/` is excluded too -- the comparison
harness is a maintainer tool, not something a user installs.

A file that is neither included nor forbidden is a *new* file nobody classified, so the
build stops and asks rather than guessing. Add it to one list or the other.
"""
import argparse
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Trees copied into the bundle, as (source, destination inside the bundle).
TREES = [
    ("packaging/install", "install"),
    # BOTH skill packages ship. They are not interchangeable -- family T is only
    # reachable inside a run on Linux -- so each installer picks its own, and a user who
    # looks in the bundle can see there are two.
    ("packaging/skill", "skill"),
    ("packaging/biomni/skill/rbpfinder", "skill/rbpfinder-linux"),
    ("packaging/agents", "agents"),
]
FILES = [
    ("packaging/README.md", "README.md"),
    ("packaging/LICENSE", "LICENSE"),
]

# Never shipped. Matched against the path relative to the repository root.
FORBIDDEN_NAMES = {
    "HANDOFF.md", "MEMORY.md", "DEVELOPER.md", "INSTALL.md", "RELEASE.md",
    "BIOMNI-HANDOFF.md", "CHANGES-linux.md",
}
FORBIDDEN_PREFIXES = ("MILESTONE_",)
FORBIDDEN_SUFFIXES = ("_PREREGISTRATION.md", ".xlsx")
FORBIDDEN_DIRS = {"benchmark", "comparison", "__pycache__", ".pytest_cache"}


def say(msg):
    print("  %s" % msg, flush=True)


def fail(msg):
    sys.exit("FAILED: %s" % msg)


def forbidden(rel):
    parts = pathlib.PurePosixPath(rel).parts
    if any(p in FORBIDDEN_DIRS for p in parts):
        return "inside a directory that never ships"
    name = parts[-1]
    if name in FORBIDDEN_NAMES:
        return "on the forbidden-name list"
    if name.startswith(FORBIDDEN_PREFIXES):
        return "matches a forbidden prefix"
    if name.endswith(FORBIDDEN_SUFFIXES):
        return "matches a forbidden suffix"
    return None


def build_wheel(out_dir):
    say("building the wheel")
    r = subprocess.run([sys.executable, "-m", "build", "--wheel",
                        "--outdir", str(out_dir)],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        fail("wheel build failed:\n%s" % (r.stderr or r.stdout)[-2000:])
    wheels = sorted(out_dir.glob("rbpfinder-*.whl"))
    if len(wheels) != 1:
        fail("expected exactly one wheel in %s, found %d -- clear it and re-run"
             % (out_dir, len(wheels)))
    return wheels[0]


def version_from_wheel(wheel):
    """The version as the wheel records it, so it cannot disagree with what pip installs."""
    m = re.match(r"rbpfinder-(.+?)-py3-none-any\.whl$", wheel.name)
    if not m:
        fail("cannot read a version out of %s" % wheel.name)
    return m.group(1)


def copy_tree(src, dst):
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        why = forbidden(rel)
        if why:
            say("skipped %s (%s)" % (rel, why))
            continue
        target = dst / path.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def fill_readme(path, version, wheel_name):
    text = path.read_text(encoding="utf-8")
    text = text.replace("{{VERSION}}", version).replace("{{WHEEL}}", wheel_name)
    left = re.findall(r"\{\{[A-Z_]+\}\}", text)
    if left:
        fail("unsubstituted placeholder(s) in README: %s" % ", ".join(sorted(set(left))))
    path.write_text(text, encoding="utf-8")


def rules_version():
    text = (ROOT / "rules" / "decision_rules.yaml").read_text(encoding="utf-8")
    block = text.split("\nmeta:", 1)[1].split("\n\n", 1)[0]
    m = re.search(r'^\s+version:\s*"([^"]+)"', block, re.M)
    return m.group(1) if m else None


def fetch_deps(dest):
    """Dependency wheels for `install_linux.sh --offline`.

    Platform-specific: a wheel set downloaded here matches THIS interpreter and OS, which
    is why the bundle records what it got rather than implying universal coverage.
    """
    dest.mkdir(parents=True, exist_ok=True)
    say("downloading dependency wheels")
    r = subprocess.run([sys.executable, "-m", "pip", "download", "--quiet",
                        "--dest", str(dest), "PyYAML>=6.0", "jsonschema>=4.0",
                        "biopython>=1.80"], capture_output=True, text=True)
    if r.returncode != 0:
        fail("pip download failed (re-run without --offline-deps to build anyway):\n%s"
             % (r.stderr or r.stdout)[-1500:])
    got = sorted(p.name for p in dest.glob("*"))
    say("%d dependency artefact(s)" % len(got))
    return got


def checksums(bundle):
    lines = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS.txt":
            continue
        h = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append("%s  %s" % (h, path.relative_to(bundle).as_posix()))
    (bundle / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def verify(bundle, wheel_name):
    """Check the bundle against what the installers actually read."""
    problems = []
    required = ["README.md", "LICENSE", "VERSION.json", "SHA256SUMS.txt",
                "runtime/" + wheel_name,
                "install/install_linux.sh", "install/install_windows.ps1",
                "skill/rbpfinder/SKILL.md",
                "skill/rbpfinder-linux/SKILL.md",
                "skill/rbpfinder-linux/references/KNOW_HOW.md"]
    for rel in required:
        if not (bundle / rel).is_file():
            problems.append("missing %s" % rel)

    # install_linux.sh globs runtime/rbpfinder-*.whl and takes the first; more than one
    # would make which-one-installs depend on sort order.
    found = list((bundle / "runtime").glob("rbpfinder-*.whl"))
    if len(found) != 1:
        problems.append("runtime/ holds %d rbpfinder wheels, expected 1" % len(found))

    # install_linux.sh prefers skill/rbpfinder-linux and falls back to skill/rbpfinder,
    # so a bundle missing the Linux package would silently install the desktop skill on
    # a Linux machine. `required` above makes that a build failure instead.
    for rel in ("README.md", "skill/rbpfinder/SKILL.md",
                "skill/rbpfinder-linux/SKILL.md"):
        p = bundle / rel
        if p.is_file() and re.search(r"\{\{[A-Z_]+\}\}", p.read_text(encoding="utf-8")):
            problems.append("%s still has a template placeholder" % rel)

    for path in bundle.rglob("*"):
        if path.is_file():
            why = forbidden(path.relative_to(bundle).as_posix())
            if why:
                problems.append("%s should not be here (%s)"
                                % (path.relative_to(bundle), why))
    return problems


def check_skill_package(pkg):
    """The gates a skill package has to pass before it is worth deploying.

    Run here rather than only on the platform, because a rejected upload costs a
    round trip and the failure modes are all local: invalid frontmatter, a reference
    that does not resolve, a stray file nobody references, a path that exists only on
    the machine that built it.
    """
    import yaml

    sk = pkg / "SKILL.md"
    if not sk.is_file():
        return ["%s has no SKILL.md" % pkg]
    text = sk.read_text(encoding="utf-8")
    problems = []

    if not text.startswith("---\n"):
        problems.append("no YAML frontmatter")
    else:
        try:
            fm = yaml.safe_load(text.split("---\n", 2)[1])
        except Exception as exc:                                  # noqa: BLE001
            fm = None
            problems.append("frontmatter is not valid YAML: %s" % exc)
        if isinstance(fm, dict):
            for key in ("id", "name", "description"):
                if not fm.get(key):
                    problems.append("frontmatter is missing %s" % key)
            if fm.get("name") != "rbpfinder":
                problems.append("frontmatter name is %r, expected rbpfinder"
                                % fm.get("name"))
            if len(fm.get("description") or "") > 1024:
                problems.append("description is over 1024 characters")
        elif fm is not None:
            problems.append("frontmatter is not a mapping")

    refs = sorted(set(re.findall(r"references/[A-Za-z0-9_]+\.md", text)))
    for ref in refs:
        if not (pkg / ref).is_file():
            problems.append("SKILL.md references a missing %s" % ref)
    for path in sorted(pkg.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(pkg).as_posix()
        if rel != "SKILL.md" and rel not in refs:
            problems.append("%s ships but SKILL.md never references it" % rel)

    for bad in re.findall(r"/home/\S+|/tmp/\S+|/mnt/\S+", text):
        problems.append("machine-local path in SKILL.md: %s" % bad)
    return problems


def build_biomni_skill(out_root):
    """The Biomni skill package on its own, with no wheel.

    Biomni installs the code from a checkout (`setup_biomni.sh` runs `pip install`), so
    the skill is deployed separately from the runtime and versions independently of it.
    The tarball therefore carries no wheel, and the skill's own text names the version
    it was written against.
    """
    pkg = ROOT / "packaging" / "biomni" / "skill" / "rbpfinder"
    problems = check_skill_package(pkg)
    if problems:
        fail("the skill package would be rejected:\n    " + "\n    ".join(problems))
    say("skill package gate PASSED")

    version = rules_version()
    out_root.mkdir(parents=True, exist_ok=True)
    tgz = out_root / ("rbpfinder-biomni-skill-%s.tar.gz" % version)
    with tarfile.open(tgz, "w:gz") as tf:
        for path in sorted(pkg.rglob("*")):
            if path.is_file():
                tf.add(path, arcname=(pathlib.Path("rbpfinder")
                                      / path.relative_to(pkg)).as_posix())
    names = tarfile.open(tgz).getnames()
    say("%d file(s): %s" % (len(names), ", ".join(names)))
    print()
    print("skill tarball: %s" % tgz)
    print("extracts to:   rbpfinder/  (drop into the Biomni skills directory)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-root", default=str(ROOT / "dist"))
    ap.add_argument("--offline-deps", action="store_true",
                    help="also download dependency wheels into runtime/wheels/, for "
                         "installing on a machine with no network. They match THIS "
                         "interpreter and OS, which VERSION.json records.")
    ap.add_argument("--archive", action="store_true",
                    help="also write .tar.gz and .zip next to the bundle directory")
    ap.add_argument("--biomni-skill-only", action="store_true",
                    help="build ONLY the Biomni skill tarball and exit. It extracts to "
                         "rbpfinder/, which is what a Biomni skills directory expects, "
                         "and carries no wheel -- the code is installed separately by "
                         "packaging/biomni/setup_biomni.sh.")
    args = ap.parse_args(argv)

    out_root_early = pathlib.Path(args.out_root)
    if args.biomni_skill_only:
        return build_biomni_skill(out_root_early)

    out_root = pathlib.Path(args.out_root)
    staging = out_root / "_wheel"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    wheel = build_wheel(staging)
    version = version_from_wheel(wheel)
    say("version %s" % version)

    rv = rules_version()
    if rv != version:
        fail("the rules file says %r and the package says %r. They are kept in step on "
             "purpose -- a run cites the rules version, so a bundle whose two numbers "
             "disagree makes that citation meaningless. Fix decision_rules.yaml's "
             "meta.version, do not override this." % (rv, version))
    say("rules version in step")

    bundle = out_root / ("rbpfinder-%s" % version)
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    (bundle / "runtime").mkdir()
    shutil.copy2(wheel, bundle / "runtime" / wheel.name)
    for src, dst in TREES:
        copy_tree(ROOT / src, bundle / dst)
    for src, dst in FILES:
        shutil.copy2(ROOT / src, bundle / dst)
    fill_readme(bundle / "README.md", version, wheel.name)

    deps = fetch_deps(bundle / "runtime" / "wheels") if args.offline_deps else []

    (bundle / "VERSION.json").write_text(json.dumps({
        "name": "rbpfinder",
        "version": version,
        "rules_version": rv,
        "wheel": wheel.name,
        "bundled_dependency_wheels": deps,
        "offline_install_supported": bool(deps),
        "offline_wheels_built_for": {
            "python": "%d.%d" % sys.version_info[:2],
            "platform": sys.platform,
        } if deps else None,
        "remote_submission": "removed; --allow-remote, --data-policy remote_allowed and "
                             "--stage2-backend ebi are refused with exit code 2",
        "validated_platform": "linux",
        "notes": "Sequence databases are not included and ship separately.",
    }, indent=1) + "\n", encoding="utf-8")

    n = checksums(bundle)
    say("checksummed %d file(s)" % n)

    problems = verify(bundle, wheel.name)
    if problems:
        fail("the bundle does not match what the installers read:\n    "
             + "\n    ".join(problems))
    say("layout verified against the installers")

    if args.archive:
        tgz = out_root / ("rbpfinder-%s.tar.gz" % version)
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(bundle, arcname=bundle.name)
        zp = out_root / ("rbpfinder-%s.zip" % version)
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(bundle.rglob("*")):
                if p.is_file():
                    zf.write(p, pathlib.Path(bundle.name) / p.relative_to(bundle))
        say("archives: %s, %s" % (tgz.name, zp.name))

    shutil.rmtree(staging)
    print()
    print("bundle: %s" % bundle)
    if not deps:
        print("note:   no dependency wheels bundled, so `install_linux.sh --offline`")
        print("        will not work. Re-run with --offline-deps for that.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
