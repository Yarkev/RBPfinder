# RBPfinder

High-recall, interpretable shortlists of candidate **receptor-binding proteins** (RBPs) in
tailed dsDNA bacteriophage genomes (*Caudoviricetes*).

RBPfinder is a rule-based classifier, not a scored ranking model. Every genomic-context,
sequence-homology, domain-profile, structural, comparative-genomics and machine-learning
signal is de-correlated into independent evidence families, then run through categorical
tier rules — no weighted totals, and no single number stands in for a judgment call. Output
is deliberately three-layered: **Primary** (the shortlist), **Rescue** (kept only to guard
against false negatives, never padded to look more confident than the evidence supports),
and **Completeness** (an honest statement of whether the evidence was even sufficient to
compress the candidate space — see `packaging/skill/rbpfinder/references/KNOWN_ISSUES.md`
for what this does not promise).

This repository ships as two separate things:

```
rbpfinder/    the Python package that does the analysis (CLI: `rbpfinder`)
rules/        every threshold, keyword list and tier condition, in one YAML file — the
              actual judgment logic; rbpfinder/*.py reads it, never hardcodes a number
packaging/    an Agent Skill (packaging/skill/rbpfinder/) that tells an AI coding agent
              (Claude Code, Codex) how to drive the installed CLI correctly, plus the
              installers for both platforms (packaging/install/)
```

The skill is an *operator*, not a second implementation. It must not re-derive tiers,
Primary/Rescue membership, or direction rules — every conclusion in a run's output came
from `rbpfinder`, not from the agent narrating one.

## Install

From source (either platform, once this repo is cloned):

```bash
pip install .
```

Or use the platform installer, which creates its own virtual environment rather than
touching the system Python:

```powershell
# Windows
cd packaging\install
.\install_windows.ps1
.\verify_install.ps1
```

```bash
# Linux
cd packaging/install
bash install_linux.sh
bash verify_install.sh
```

Both installers take `-Skill user` / `--skill user` to also drop the agent skill into
`~/.claude/skills/rbpfinder/` (or `-Skill project` / `--skill project` for one project
only). See `packaging/skill/rbpfinder/references/USER_GUIDE.md` for what to do next, and
`ADVANCED.md` for `--stage2-manifest` replay and bulk multi-genome runs.

## Two profiles

| Profile | You need | Sequences leave the machine? |
|---|---|---|
| **remote** (default) | this repo, installed | yes — to EMBL-EBI, after an explicit disclosure and per-run authorization |
| **local** | this repo + BLAST+ + a sequence database package | no |

Sequence databases are not part of this repository. The remote profile is the default
deliberately, so a new install needs no multi-hundred-megabyte database download; choose
local when the sequences are unpublished or confidential — see
`packaging/skill/rbpfinder/references/DATA_POLICY.md`.

## Use it directly

```bash
rbpfinder --genbank phage_X.gbk --phage-id X --out runs/X
```

Or, once the skill is installed, ask an agent in plain language: *"Analyse phage_X.gbk
with RBPfinder. The host is Stenotrophomonas maltophilia. EBI remote Stage 2 is allowed."*

## Scope and limits

```
Organisms   tailed dsDNA bacteriophages (Caudoviricetes) only
Platform    Windows validated; Linux and macOS not yet validated on a real target
Output      prioritised experimental candidates -- not validated biology
```

RBPfinder does not promise to find every real RBP in every phage. When the evidence is
insufficient it says `completeness: limited` and explains why, rather than padding the
list — read that state; it is the honest part of the result. See
`packaging/skill/rbpfinder/references/KNOWN_ISSUES.md` for the full list of what v1 does
not do.

## Licence

Proprietary — see `LICENSE`.
