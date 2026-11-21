#!/usr/bin/env bash
# Set RBPfinder up inside a Biomni hosted sandbox (controllable Linux, no root needed).
#
# Idempotent: safe to re-run. It skips whatever is already in place, and on a rebuilt
# machine (the sandbox is wiped on delete / OOM) it restores the large databases from a
# shared-storage backup instead of downloading them again. Everything lands under
# $RBP_HOME (default /workspace), which survives hibernation.
#
#   bash setup_biomni.sh                      # install into /workspace, download DBs
#   RBP_HOME=/workspace bash setup_biomni.sh
#   RBP_BACKUP=/shared/rbpfinder bash setup_biomni.sh   # also back up / restore DBs there
#
# It installs software and downloads structure databases. It submits nothing and contacts
# no sequence service. After it finishes, `source $RBP_HOME/rbpfinder.env` (it prints the
# line) puts rbpfinder and foldseek on PATH with the databases configured.
set -euo pipefail

RBP_HOME="${RBP_HOME:-/workspace}"
RBP_BACKUP="${RBP_BACKUP:-}"            # optional shared-storage dir for DB backup/restore
BIN_DIR="$RBP_HOME/bin"
FS_DIR="$RBP_HOME/foldseek"
VENV="$RBP_HOME/rbpfinder-venv"
FOLDSEEK_URL="${FOLDSEEK_URL:-https://mmseqs.com/foldseek/foldseek-linux-avx2.tar.gz}"
# The repo root: this script lives in packaging/biomni, so two levels up is the source.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

say() { printf '==> %s\n' "$1"; }

mkdir -p "$BIN_DIR" "$FS_DIR"

# --- 1. rbpfinder in its own venv -------------------------------------------------
# EDITABLE on purpose. This script used to do a plain `pip install "$REPO"`, which walked
# straight into the trap README section 2a warns about: a non-editable copy shadows the
# checkout, so `import rbpfinder` resolves to the copy and a later `git checkout` changes
# nothing that runs. A real test round was verified against stale code that way.
#
# The old reuse guard made it worse. It only asked whether the executable existed, so
# after a `git checkout` it printed "already installed -- reusing" and the new code was
# never installed -- silently. The guard now checks WHERE the import resolves to, which
# is the same question section 2a asks a human, asked by the script about itself.
if [ -x "$VENV/bin/rbpfinder" ]; then
    resolved="$("$VENV/bin/python" - <<'EOF' 2>/dev/null || true
import inspect
try:
    import rbpfinder
    print(inspect.getsourcefile(rbpfinder) or "")
except Exception:
    print("")
EOF
)"
    case "$resolved" in
        "$REPO"/*) say "rbpfinder already installed editable from $REPO -- reusing"
                   needs_install=0 ;;
        "")        say "rbpfinder present but not importable -- reinstalling"
                   needs_install=1 ;;
        *)         say "rbpfinder imports from $resolved, NOT from $REPO -- reinstalling"
                   say "  (a non-editable copy shadows the checkout; see README 2a)"
                   needs_install=1 ;;
    esac
else
    needs_install=1
fi

if [ "$needs_install" = "1" ]; then
    say "installing rbpfinder (editable) into $VENV"
    [ -d "$VENV" ] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet --force-reinstall --no-deps -e "$REPO"
    "$VENV/bin/pip" install --quiet -e "$REPO"
fi

# --- 2. foldseek static binary ----------------------------------------------------
if [ ! -x "$BIN_DIR/foldseek" ]; then
    say "installing foldseek static binary into $BIN_DIR"
    tmp="$(mktemp -d)"
    curl -fsSL "$FOLDSEEK_URL" -o "$tmp/foldseek.tar.gz"
    tar -xzf "$tmp/foldseek.tar.gz" -C "$tmp"
    install -m 0755 "$tmp/foldseek/bin/foldseek" "$BIN_DIR/foldseek"
    rm -rf "$tmp"
else
    say "foldseek already present at $BIN_DIR/foldseek -- reusing"
fi
export PATH="$BIN_DIR:$PATH"

# --- 3. structure database + ProstT5 weights --------------------------------------
# Restore from backup first (fast, survives an OOM rebuild); download only if neither the
# live copy nor a backup exists. foldseek's own `databases` command assembles each one.
restore_or_build() {
    name="$1"; dest="$2"; foldseek_db_name="$3"
    if [ -e "${dest}.dbtype" ] || [ -e "$dest" ]; then
        say "$name already present at $dest -- reusing"
        return
    fi
    if [ -n "$RBP_BACKUP" ] && [ -d "$RBP_BACKUP/$name" ]; then
        say "restoring $name from backup $RBP_BACKUP/$name"
        cp -a "$RBP_BACKUP/$name/." "$(dirname "$dest")/"
        return
    fi
    say "downloading $name via foldseek databases $foldseek_db_name (this is the slow step)"
    "$BIN_DIR/foldseek" databases "$foldseek_db_name" "$dest" "$FS_DIR/tmp_$name"
    if [ -n "$RBP_BACKUP" ]; then
        say "backing $name up to $RBP_BACKUP/$name"
        mkdir -p "$RBP_BACKUP/$name"
        cp -a "$(dirname "$dest")"/"$(basename "$dest")"* "$RBP_BACKUP/$name/"
    fi
}

# pdb100 is the functional database RBPfinder's Stage 3 needs (hits carry a title it can
# read for receptor-binding identity). ProstT5 lets an amino-acid query be searched with
# no AlphaFold step.
restore_or_build "pdb100"  "$FS_DIR/pdb100"  "PDB"
restore_or_build "prostt5" "$FS_DIR/prostt5" "ProstT5"

# --- 4. the environment file ------------------------------------------------------
ENV_FILE="$RBP_HOME/rbpfinder.env"
cat > "$ENV_FILE" <<EOF
# source this to use RBPfinder with local structure search
export PATH="$VENV/bin:$BIN_DIR:\$PATH"
export RBPFINDER_FOLDSEEK="$BIN_DIR/foldseek"
export RBPFINDER_FOLDSEEK_DB="pdb100=$FS_DIR/pdb100"
export RBPFINDER_PROSTT5_MODEL="$FS_DIR/prostt5"

# Keep the database registry under $RBP_HOME, which survives hibernation.
#
# \`rbpfinder database configure\` writes to \$XDG_CONFIG_HOME or ~/.config by default,
# and in this sandbox that path is NOT in the persistent snapshot -- so a hibernate/resume
# silently loses the DB_PHAGE registration while the database files themselves are still
# sitting there, and doctor goes back to reporting NOT CONFIGURED. Observed 2026-11-17.
export RBPFINDER_CONFIG="$RBP_HOME/rbpfinder-databases.yaml"

# Sequence databases for family S. S is what carries FUNCTIONAL homolog annotations, and
# those are what lift a candidate above apparatus membership -- without it a real RBP
# stalls at R3 however good its structural hit. Register one with:
#     rbpfinder database configure phage <blast-db-prefix>
# (and \`host\` if you have a database covering the phage's host genus). With
# RBPFINDER_CONFIG set above, that registration is durable. Alternatively set the paths
# directly here, which takes precedence over the registry:
# export RBPFINDER_DB_PHAGE="$RBP_HOME/db/phage"
# export RBPFINDER_DB_HOST="$RBP_HOME/db/host"
EOF

say "done."
echo
echo "  source $ENV_FILE"
echo "  rbpfinder doctor        # expect: Local Stage 3  AVAILABLE"
echo
echo "Nothing was submitted anywhere. Structure search runs on this machine."
