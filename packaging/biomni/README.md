# RBPfinder on Biomni

Running RBPfinder inside a Biomni hosted sandbox, with **local Foldseek structure
search** so family-T evidence is computed on the machine — no AlphaFold step, no web
handoff, nothing transmitted, and no tokens spent driving a website.

## How the platform loads this

The hosted platform reads a **skill package**: `SKILL.md` (plus any reference files) under
`/mnt/results/skills/<slug>/`. That is what `check_skill` validates and what the agent
retrieves.

> The open-source Biomni `A1` agent also exposes `agent.add_tool(fn)` for registering a
> Python callable. **That API is not present in this platform's installed `biomni.tool`**
> — calling it raises `AttributeError`. An earlier version of this directory shipped an
> `add_tool` layer; it was removed rather than left as dead code. If you self-host
> upstream Biomni, that route exists, but nothing here depends on it.

## 1. Set the sandbox up (once per machine)

The sandbox is controllable — a static Foldseek binary (~30 MB, no root), tens of GB of
disk. `/workspace` survives hibernation; it is wiped only on a machine delete / OOM
rebuild, which is why the script backs the databases up to shared storage and restores
from there instead of re-downloading.

```bash
# from the cloned repo
RBP_BACKUP=/shared/rbpfinder bash packaging/biomni/setup_biomni.sh
source /workspace/rbpfinder.env
rbpfinder doctor            # expect: Local Stage 3  AVAILABLE
```

`setup_biomni.sh` installs rbpfinder into its own venv, drops the Foldseek binary in
`/workspace/bin`, and assembles `pdb100` + the ProstT5 weights with `foldseek databases`.
It is idempotent: re-run it any time, and after a rebuild it restores the databases from
`$RBP_BACKUP` in seconds. It submits nothing.

> No GPU: ProstT5's sequence→3Di step is CPU-bound, so RBPfinder searches only the
> **nominated candidate pool** by default, not every CDS — much less work, same result.
> `--foldseek-all-cds` forces genome-wide.

Also install a sequence database for family S (local BLAST) — without one, and with
remote disabled, S can never be earned at all.

What a database does **not** do by itself is raise a tier. Measured on T7 with a full
RefSeq viral protein database: gp17's top-10 homologs are all annotated "tail fiber
protein", so S reads apparatus and gp17 stays at `R3`. Extra families raise `tier_e`;
only a receptor-binding direction raises `tier_r`. For that you need a *functional*
annotation — which on T7 came from running phold, not from any database:

```bash
rbpfinder database configure phage <blast-db-prefix>
```

> **The registry must live under `/workspace`.** `database configure` writes to
> `$XDG_CONFIG_HOME` or `~/.config` by default, which this sandbox does **not** keep in
> its persistent snapshot: a hibernate/resume loses the registration while the database
> files themselves are still there, and `doctor` goes back to reporting
> `NOT CONFIGURED`. `setup_biomni.sh` therefore sets `RBPFINDER_CONFIG` to a path inside
> the prefix, so `source /workspace/rbpfinder.env` before configuring and the
> registration is durable. (`RBPFINDER_DB_PHAGE` / `RBPFINDER_DB_HOST` set directly in
> that env file also work, and take precedence over the registry.)

## 2. Install the skill package

```bash
mkdir -p /mnt/results/skills/rbpfinder/references
cp packaging/biomni/skill/rbpfinder/SKILL.md            /mnt/results/skills/rbpfinder/
cp packaging/biomni/skill/rbpfinder/references/*.md     /mnt/results/skills/rbpfinder/references/
python3 /mnt/skills/system/phylo-create-skill/scripts/check_skill.py \
        /mnt/results/skills/rbpfinder/
```

### Copying the files is NOT registration

This was wrong here until 2026-11-21, and a real deployment hit it: the files were in
place, `check_skill.py` passed, and **the skill did not appear in the skills list**. The
two commands above do exactly two things, and neither is registration:

* the `cp`s put the package where the platform expects to find it;
* `check_skill.py` validates the package's structure and its internal consistency.

Registration is a **separate platform flow**: the agent generates a preview card and
**you confirm the save**. Ask for it in as many words — *"now register this skill on the
platform"* — and confirm when the card appears. Until you do, the package is a correctly
shaped directory that nothing has been told about.

**The `id` in the frontmatter is not what registers it.** An earlier version of this file
claimed the frontmatter `id` meant deploying over the existing directory updates in place.
It does not: the platform assigns its **own** id on save, so the id carried in the file is
whichever environment the package last came from. It is informational. Do not reason about
re-registration from it, and do not expect a redeploy of the files alone to update a
registered skill.

`skill/rbpfinder/` is the version-controlled source of truth; the copy under
`/mnt/results/` is a deployment. Keep edits in the repo and redeploy, so the registered
skill and the code it drives cannot drift.

## 2a. Verify the deployment, do not assume it

**The skill and the code must be at the same commit.** The skill documents flags; if the
installed code is older, an agent following it hits an argparse error on a flag the
documentation promises. Deploying one without the other is the most likely way this goes
wrong.

```bash
# which source is actually imported -- NOT which one you edited
python -c "import rbpfinder, inspect; print(inspect.getsourcefile(rbpfinder))"

# and what a run says about itself
rbpfinder --genbank <any.gbk> --phage-id T --out /tmp/t --no-local-stage2 \
          --quiet-progress &&
python -c "import json; print(json.load(open('/tmp/t/run_result.json'))['versions'])"
```

The import path must point inside your checkout, and `versions.package` must match the
commit you meant to deploy. A test round was once verified **against the old code** and
reported as passing: a non-editable copy had been installed into the venv and shadowed the
editable path, so `import rbpfinder` resolved to the copy and `git checkout` changed
nothing that ran. `uv pip install -e` survives a branch switch with no reinstall; changing
*install method* is what needs this check.

`versions.package` reading `0+unknown` means the tool is running from a source tree that
was never installed — not a released version, and any number it produces is
unattributable.

## 3. Ask

```
Find the receptor-binding protein in /data/G368-2.fasta.
Host is Acinetobacter baumannii.
```

The flow the skill prescribes:

```
ask the user: fast (default) or accurate?
  |  fast                                     accurate
  v                                             v
pharokka                                  pharokka -> phold      (the agent runs these)
  |                                             |
  v  pharokka.gbk                               v  phold.gbk
rbpfinder validate-input          -> usable? seconds, precise reason if not
  v
rbpfinder --genbank ...           -> Primary / Rescue / completeness
```

**Biomni annotates; RBPfinder decides what is an RBP.** Keeping the annotator outside
RBPfinder means a pharokka or PHROGs upgrade cannot quietly move a tier.

### fast vs accurate

| | fast (default) | accurate |
|---|---|---|
| Annotation | pharokka | pharokka + phold (genome-wide) |
| Extra compute | — | **1–2 h on 8 CPU cores** |
| Structural evidence | yes — local Foldseek over the candidate pool | yes — the same local Foldseek step |
| What the extra time buys | — | better annotation: resolves CDS pharokka leaves as "hypothetical", improving nomination and the direction read off a product name |

phold does **not** add a second independent structural evidence family: de-correlation
collapses a phold hit and a Foldseek hit to one family, by design. Reach for `accurate`
when the genome is poorly annotated, or when a fast run came back `completeness: limited`.

> Hand over phold's **GenBank**, never its per-CDS predictions TSV. The table carries no
> protein sequence, so local structure search would be silently unavailable.

## Remote submission is disabled in this build

`--allow-remote`, `--data-policy remote_allowed` and `--stage2-backend ebi` are refused
with exit code 2, before any ingest or provider code runs. Valid modes: `private`
(default) and `import_only`. On a server that can download Foldseek, a structure database
and a BLAST database, every evidence family is reachable locally — including HHpred and
InterProScan, whose "manual only" status upstream is a *Windows* limitation, not a Linux
one — so nothing is given up.

## Scope

Tailed dsDNA phages (*Caudoviricetes*) only. Input must be an **annotated** GenBank —
pharokka output or an NCBI RefSeq record with CDS `/translation` features. A bare genome
FASTA is refused: annotate it first. Local structure search is Linux-only (Foldseek has
no native Windows build).

## Files

| File | What it is |
|---|---|
| `skill/rbpfinder/SKILL.md` | the skill package the platform registers |
| `skill/rbpfinder/references/KNOW_HOW.md` | the procedure and the failure modes it prevents |
| `setup_biomni.sh` | idempotent `/workspace` install + database download/restore |
