# RBPfinder in Biomni

Running RBPfinder inside a [Biomni](https://github.com/snap-stanford/Biomni) hosted
sandbox, with **local Foldseek structure search** so family-T evidence is computed on the
machine — no AlphaFold step, no web handoff, nothing transmitted, and no tokens spent
driving a website.

Biomni does not read an Agent Skill's `SKILL.md`. It takes a custom tool (a documented
Python callable registered with `agent.add_tool`), plus software and data declarations.
This directory provides all three, and a one-shot setup script for the sandbox.

## 1. Set the sandbox up (once per machine)

The sandbox is controllable — a static Foldseek binary (~30 MB, no root), tens of GB of
disk for the database. `/workspace` survives hibernation; it is wiped only on a machine
delete / OOM rebuild, which is why the script backs the databases up to shared storage
and restores from there instead of re-downloading.

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
> `--foldseek-all-cds` forces genome-wide if you ever want it.

## 2. Register the tools with the agent

```python
import sys
sys.path.append("packaging/biomni")          # or copy rbpfinder_biomni.py onto PYTHONPATH

from biomni.agent import A1
from rbpfinder_biomni import register

agent = A1(path="./biomni_data", llm="claude-sonnet-4-5-20250929")
register(agent)                               # adds the tools + software + database entries
```

`register` adds three tools, meant to be called in order:

| Tool | What it does |
|---|---|
| `rbpfinder_workflow_plan(mode=None)` | settles **fast vs accurate** with the user and returns the exact annotation commands and the required deliverable |
| `check_annotation(genbank_path=...)` | seconds-long precondition check, so a bad annotation fails *before* phold or the analysis |
| `run_rbpfinder(...)` | the analysis; returns Primary / Rescue / completeness |

Also copy `know_how/phage_rbp_identification.md` into your Biomni know-how library: it is
the procedure and the scientific invariants the agent should follow, including the failure
modes it must not reproduce.

`register(agent)` reads `RBPFINDER_FOLDSEEK_DB` and `RBPFINDER_PROSTT5_MODEL` from the
environment (set by `rbpfinder.env`), so run step 1 and `source` the env file in the same
process, or export those three variables before constructing the agent.

## 3. Ask

```python
agent.go(
    "Find the receptor-binding protein in /data/G368-2.fasta. "
    "Host is Acinetobacter baumannii."
)
```

The standardised flow the agent then follows:

```
rbpfinder_workflow_plan()        -> asks you: fast (default) or accurate?
  |  fast                                     accurate
  v                                             v
pharokka                                  pharokka -> phold        (Biomni runs these)
  |                                             |
  v  pharokka.gbk                               v  phold.gbk
check_annotation()               -> usable? seconds, fails with a precise reason
  v
run_rbpfinder(mode=...)          -> Primary / Rescue / completeness
```

**The division of labour is deliberate.** Biomni annotates; RBPfinder decides what is an
RBP. Keeping the annotator outside RBPfinder means a pharokka or PHROGs upgrade cannot
quietly move a tier, and the judgment layer stays frozen, auditable and reproducible.

### fast vs accurate

| | fast (default) | accurate |
|---|---|---|
| Annotation | pharokka | pharokka + phold (genome-wide) |
| Extra compute | — | **1–2 h on 8 CPU cores** |
| Structural evidence | yes — local Foldseek over the candidate pool | yes — the same local Foldseek step |
| What the extra time buys | — | better annotation: resolves CDS pharokka leaves as "hypothetical", improving nomination and the direction read off a product name |

phold does **not** add a second independent structural evidence family: RBPfinder's
de-correlation collapses a phold hit and a Foldseek hit to one family, by design. Reach
for `accurate` when the genome is poorly annotated, or when a fast run came back
`completeness: limited`.

> Hand over phold's **GenBank**, never its per-CDS predictions TSV. The table carries no
> protein sequence, so local structure search would be silently unavailable. `phold.gbk`
> has phold's improved products *and* the translations.

## What the tool will and won't do

`run_rbpfinder` is an **operator** of the CLI, exactly like the Agent Skill. It runs one
analysis and reports what came out. It does not re-derive tiers, Primary/Rescue membership,
direction, or completeness, and the tool's own result carries the invariants the agent
must preserve when it summarises:

- Primary is the shortlist to test first; **Rescue only protects recall** and its evidence
  is not strong — do not expand it to make recall look better.
- The **completeness** state (`complete` / `partial` / `limited`) is honest. A `limited`
  result is the real signal, not a failure, and must never be presented as biological
  completeness.
- `searched-no-hit` is not `not-run`; absence of evidence never lowers a tier; one
  sequence-homology family stays one family however many databases were searched.

## Scope

Tailed dsDNA phages (Caudoviricetes) only. Input must be an **annotated** GenBank (CDS
`/translation` features, e.g. pharokka output) — a bare genome is refused. Linux only for
the local structure path (Foldseek has no native Windows build).

## Files

| File | What it is |
|---|---|
| `rbpfinder_biomni.py` | the `run_rbpfinder` custom tool and `register(agent)` |
| `setup_biomni.sh` | idempotent `/workspace` install + database download/restore |
