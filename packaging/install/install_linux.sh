#!/usr/bin/env bash
# RBPfinder installer (Linux).
#
# Installs the runtime into its OWN virtual environment. Not into the system Python:
# RBPfinder pins three dependencies, and sharing an interpreter with whatever else is on
# the machine is how one tool's upgrade silently changes another tool's results.
#
#     ./install_linux.sh                          online, default location ($HOME/rbpfinder)
#     ./install_linux.sh --offline                 use the bundled wheels
#     ./install_linux.sh --prefix /srv/rbpfinder    install somewhere else
#     ./install_linux.sh --skill project            also install the agent skill here
#     ./install_linux.sh --skill user                ... or for all your projects
#     ./install_linux.sh --python /usr/bin/python3.11
#
# This script installs software. It downloads no sequence databases, submits nothing, and
# contacts EMBL-EBI never.
set -euo pipefail

PREFIX="$HOME/rbpfinder"
OFFLINE=0
SKILL="none"
SKILL_TARGET="."
PYTHON_EXE=""

say() { printf '%s\n' "$1"; }
fail() { printf '\nFAILED: %s\n' "$1" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        --offline) OFFLINE=1; shift ;;
        --skill) SKILL="$2"; shift 2 ;;
        --skill-target) SKILL_TARGET="$2"; shift 2 ;;
        --python) PYTHON_EXE="$2"; shift 2 ;;
        *) fail "unrecognised argument: $1" ;;
    esac
done
case "$SKILL" in
    none|project|user) ;;
    *) fail "--skill must be one of: none, project, user (got: $SKILL)" ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"

say "=== RBPfinder installer ==="
say "  bundle   $ROOT"
say "  prefix   $PREFIX"
if [ "$OFFLINE" -eq 1 ]; then
    say "  mode     offline (bundled wheels)"
else
    say "  mode     online (PyPI for dependencies)"
fi

# --- 1. find a usable Python -------------------------------------------------------
# Probed by actually running it and checking the reported version, not just checking that
# the name resolves on PATH -- the same discipline the Windows installer uses against the
# Microsoft Store Python stub, kept here for the analogous case of a `python3` alias that
# points at something unexpected (a pyenv shim with no version selected, a conda base that
# isn't actually activated).
CANDIDATES=()
[ -n "$PYTHON_EXE" ] && CANDIDATES+=("$PYTHON_EXE")
CANDIDATES+=("python3.13" "python3.12" "python3.11" "python3.10" "python3.9" "python3" "python")

PYTHON=""
TRIED=()
for cand in "${CANDIDATES[@]}"; do
    command -v "$cand" >/dev/null 2>&1 || continue
    TRIED+=("$cand")
    ver="$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    [ -z "$ver" ] && continue
    major="${ver%%.*}"
    minor="${ver##*.}"
    if [ "$major" -gt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -ge 9 ]; }; then
        PYTHON="$cand"
        PYTHON_VERSION="$ver"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    say ""
    say "  probed, and none of these was a working Python 3.9 or newer:"
    # `${#TRIED[@]}` guard, not a bare `"${TRIED[@]}"` expansion: on bash < 4.4 (still the
    # system bash on e.g. RHEL7/CentOS7), expanding an empty array under `set -u` is a hard
    # "unbound variable" error -- and TRIED being empty (no candidate even resolved on
    # PATH) is exactly the failure this branch exists to report gracefully.
    if [ "${#TRIED[@]}" -gt 0 ]; then
        for t in "${TRIED[@]}"; do say "    $t"; done
    else
        say "    (nothing named python3.9+ through python3.13, python3 or python was found on PATH)"
    fi
    fail "no usable Python found. Install one with your distro's package manager (e.g. apt install python3.11), or point this script at an existing interpreter with --python <path>"
fi
say "  python   $PYTHON -> $PYTHON_VERSION"

# --- 2. create the venv --------------------------------------------------------------
VENV="$PREFIX/venv"
VENV_PY="$VENV/bin/python"
VENV_EXE="$VENV/bin/rbpfinder"

if [ -x "$VENV_PY" ]; then
    say ""
    say "  an environment already exists at $VENV -- reusing it."
    say "  (delete that directory yourself if you want a clean install; this script"
    say "   will not remove it for you)"
else
    say ""
    say "  creating $VENV"
    "$PYTHON" -m venv "$VENV" || fail "could not create the virtual environment."
fi

# --- 3. install ------------------------------------------------------------------------
WHEEL_DIR="$ROOT/runtime"
WHEEL="$(find "$WHEEL_DIR" -maxdepth 1 -name 'rbpfinder-*.whl' | sort | head -n 1)"
[ -z "$WHEEL" ] && fail "no rbpfinder wheel found in $WHEEL_DIR"
say ""
say "  installing $(basename "$WHEEL")"

if [ "$OFFLINE" -eq 1 ]; then
    WHEELS_SUB="$WHEEL_DIR/wheels"
    [ -d "$WHEELS_SUB" ] || fail "--offline was given but $WHEELS_SUB does not exist. This bundle may have been built without the dependency wheels."
    # Two --find-links: dependencies in wheels/, the runtime wheel one level up.
    "$VENV_PY" -m pip install --quiet --no-index \
        --find-links "$WHEELS_SUB" --find-links "$WHEEL_DIR" rbpfinder \
        || fail "pip install failed."
else
    "$VENV_PY" -m pip install --quiet "$WHEEL" \
        || fail "pip install failed. If this machine has no internet access, re-run with --offline."
fi

# --- 4. install the agent skill, if asked ----------------------------------------------
if [ "$SKILL" != "none" ]; then
    SRC="$ROOT/skill/rbpfinder"
    if [ "$SKILL" = "user" ]; then
        DST="$HOME/.claude/skills/rbpfinder"
    else
        DST="$(cd "$SKILL_TARGET" && pwd)/.claude/skills/rbpfinder"
    fi
    say ""
    say "  installing the agent skill -> $DST"
    mkdir -p "$DST"
    cp -r "$SRC"/. "$DST"/
    if [ "$SKILL" != "user" ]; then
        AGENTS_DST="$(cd "$SKILL_TARGET" && pwd)"
        for f in CLAUDE.md AGENTS.md; do
            if [ -f "$AGENTS_DST/$f" ]; then
                say "  $f already exists here -- left alone, not overwritten"
            else
                cp "$ROOT/agents/$f" "$AGENTS_DST/$f"
                say "  wrote $f"
            fi
        done
    fi
fi

say ""
say "=== installed ==="
say "  rbpfinder    $VENV_EXE"
say ""
say "Add it to PATH for this session with:"
say "    export PATH=\"$VENV/bin:\$PATH\""
say ""
say "Verify with:"
say "    ./verify_install.sh --prefix $PREFIX"
say ""
say "Nothing has been submitted anywhere and no sequence database was downloaded."
say "Remote Stage 2 asks for authorization the first time you use it."
