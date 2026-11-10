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
if [ ! -x "$VENV/bin/rbpfinder" ]; then
    say "installing rbpfinder into $VENV"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet "$REPO"
else
    say "rbpfinder already installed in $VENV -- reusing"
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
EOF

say "done."
echo
echo "  source $ENV_FILE"
echo "  rbpfinder doctor        # expect: Local Stage 3  AVAILABLE"
echo
echo "Nothing was submitted anywhere. Structure search runs on this machine."
