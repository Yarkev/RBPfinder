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
remote disabled, S can never be earned. `rbpfinder database configure phage <prefix>`.

## 2. Install the skill package

```bash
mkdir -p /mnt/results/skills/rbpfinder
cp packaging/biomni/skill/rbpfinder/* /mnt/results/skills/rbpfinder/
```

`skill/rbpfinder/` is the version-controlled source of truth; the copy under
`/mnt/results/` is a deployment. Keep edits in the repo and redeploy, so the registered
skill and the code it drives cannot drift.

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
| `skill/rbpfinder/KNOW_HOW.md` | the procedure and the failure modes it prevents |
| `setup_biomni.sh` | idempotent `/workspace` install + database download/restore |
