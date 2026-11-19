#!/usr/bin/env bash
# Verify an RBPfinder installation (Linux).
#
# Reports only. It runs no analysis, downloads nothing, submits nothing.
#
#     ./verify_install.sh
#     ./verify_install.sh --prefix /srv/rbpfinder
set -uo pipefail

PREFIX="$HOME/rbpfinder"
while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        *) echo "unrecognised argument: $1" >&2; exit 2 ;;
    esac
done

VENV_PY="$PREFIX/venv/bin/python"
VENV_EXE="$PREFIX/venv/bin/rbpfinder"

PASS=0
FAIL=0
# check <name> <exit-code-as-integer, 0 = ok> <detail>. The caller captures its own exit
# code into a plain variable immediately after running the command being checked -- never
# chained through $? across a later line or a nested subshell, which is exactly the
# fragile pattern this project's own conventions (gate-runner-no-pipe-rule) warn against.
check() {
    if [ "$2" -eq 0 ]; then
        printf '  OK   %-46s %s\n' "$1" "$3"
        PASS=$((PASS + 1))
    else
        printf '  FAIL %-46s %s\n' "$1" "$3"
        FAIL=$((FAIL + 1))
    fi
}

echo "=== verifying RBPfinder at $PREFIX ==="

if [ -x "$VENV_PY" ]; then venv_rc=0; else venv_rc=1; fi
check "virtual environment exists" "$venv_rc" "$VENV_PY"
if [ "$venv_rc" -ne 0 ]; then
    echo ""
    echo "VERIFY_INSTALL=FAIL  (nothing is installed here)"
    exit 1
fi

if [ -x "$VENV_EXE" ]; then exe_rc=0; else exe_rc=1; fi
check "rbpfinder command exists" "$exe_rc" "$VENV_EXE"

# Import from a DIFFERENT working directory on purpose. Importing while standing in the
# source tree proves the source tree works, not that the installation does.
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
pushd "$TMP" >/dev/null

ver="$("$VENV_PY" -c "import rbpfinder; print(getattr(rbpfinder,'__version__','?'))" 2>&1)"
ver_rc=$?
check "package imports outside its source tree" "$ver_rc" "$ver"

rules="$("$VENV_PY" -c "
import importlib.resources as r
p = r.files('rbpfinder') / 'rules' / 'decision_rules.yaml'
print(p.is_file() and p.stat().st_size or 0)
" 2>&1)"
rules_rc=$?
rules_num="$(printf '%s' "$rules" | tr -dc '0-9')"
if [ "$rules_rc" -eq 0 ] && [ -n "$rules_num" ] && [ "$rules_num" -gt 10000 ]; then
    rules_check_rc=0
else
    rules_check_rc=1
fi
check "packaged decision_rules.yaml resolves" "$rules_check_rc" "${rules} bytes"

schema="$("$VENV_PY" -c "
import importlib.resources as r
p = r.files('rbpfinder') / 'rules' / 'evidence_schema.json'
print(p.is_file() and p.stat().st_size or 0)
" 2>&1)"
schema_rc=$?
schema_num="$(printf '%s' "$schema" | tr -dc '0-9')"
if [ "$schema_rc" -eq 0 ] && [ -n "$schema_num" ] && [ "$schema_num" -gt 100 ]; then
    schema_check_rc=0
else
    schema_check_rc=1
fi
check "packaged evidence_schema.json resolves" "$schema_check_rc" "${schema} bytes"

for mod in yaml jsonschema Bio; do
    "$VENV_PY" -c "import $mod" >/dev/null 2>&1
    mod_rc=$?
    check "dependency importable: $mod" "$mod_rc" ""
done

# doctor reports; it must not change anything. Exit 0 or 3 are both legitimate -- 3 means
# an OPTIONAL capability is missing, which is a normal remote-profile install.
doctor_out="$("$VENV_EXE" doctor 2>&1)"
dcode=$?
if [ "$dcode" -eq 0 ] || [ "$dcode" -eq 3 ]; then doctor_check_rc=0; else doctor_check_rc=1; fi
check "doctor runs" "$doctor_check_rc" "exit $dcode"
if printf '%s' "$doctor_out" | grep -q "blastp"; then
    echo "       (doctor mentions blastp -- see its output for whether a local"
    echo "        database is configured. Without one there is no family S, and"
    echo "        there is no remote route -- it was removed)"
fi

# These two used to assert that --help DOCUMENTED the EBI contact email and
# --allow-remote. Remote submission has since been removed from the tool, so those checks
# were requiring the install to advertise a capability that exits 2 -- a gate that would
# have failed the day someone correctly took the flags out. They now verify the refusal
# instead, which is the property a user actually depends on.
TMP_REFUSE="$(mktemp -u)"
help_out="$("$VENV_EXE" --help 2>&1)"
if printf '%s' "$help_out" | grep -q "REMOVED"; then
    help_rc=0
else
    help_rc=1
fi
check "--help marks the remote backend as removed" "$help_rc" ""

"$VENV_EXE" --genbank /dev/null --phage-id V --out "$TMP_REFUSE" --allow-remote \
    >/dev/null 2>"$TMP_REFUSE.err"
refuse_rc=$?
if [ "$refuse_rc" -eq 2 ] && grep -q "remote submission is disabled" "$TMP_REFUSE.err"; then
    refuse_ok=0
else
    refuse_ok=1
fi
check "--allow-remote is refused with exit 2" "$refuse_ok" "got exit $refuse_rc"

popd >/dev/null

echo ""
echo "VERIFY_INSTALL=$([ "$FAIL" -eq 0 ] && echo PASS || echo FAIL)  ($PASS/$((PASS + FAIL)))"
[ "$FAIL" -eq 0 ] || exit 1
