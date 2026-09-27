# RBPfinder {{VERSION}}

High-recall, interpretable shortlists of candidate **receptor-binding proteins** in tailed
dsDNA bacteriophage genomes.

This bundle contains two separate things:

```
runtime/   the Python software that does the analysis
skill/     an Agent Skill telling Claude Code or Codex how to drive it correctly
```

The split matters. The agent is an operator; the scientific judgement stays in the frozen
executor. **The model must not re-derive tiers, Primary/Rescue, or direction rules** — if a
conclusion did not come out of a run, it did not come from RBPfinder.

## Install (Windows)

```powershell
cd install
.\install_windows.ps1
.\verify_install.ps1
```

Offline machine, or no PyPI access:

```powershell
.\install_windows.ps1 -Offline
```

Install the agent skill at the same time:

```powershell
.\install_windows.ps1 -Skill project -SkillTarget C:\my\analysis\folder
.\install_windows.ps1 -Skill user      # available in all your projects
```

The installer creates its own virtual environment at `C:\rbpfinder\venv` (override with
`-Prefix`). It downloads no sequence databases and contacts EMBL-EBI never.

## Install (Linux)

```bash
cd install
bash install_linux.sh
bash verify_install.sh
```

Offline machine, or no PyPI access:

```bash
bash install_linux.sh --offline
```

Install the agent skill at the same time:

```bash
bash install_linux.sh --skill project --skill-target /my/analysis/folder
bash install_linux.sh --skill user      # available in all your projects
```

The installer creates its own virtual environment at `$HOME/rbpfinder/venv` (override with
`--prefix`). It downloads no sequence databases and contacts EMBL-EBI never.

## Install the skill by hand

**Claude Code** — copy `skill/rbpfinder/` to either:

```
<project>/.claude/skills/rbpfinder/       this project only
~/.claude/skills/rbpfinder/               every project
```

and put `agents/CLAUDE.md` in the project root.

**Codex** — install `skill/rbpfinder/` as a Skill, and put `agents/AGENTS.md` in the
project root.

Both read the same `SKILL.md`; there is one workflow document, not two.

## Use it

Once installed, ask in plain language:

```
Analyse phage_X.gbk with RBPfinder.
The host is Stenotrophomonas maltophilia.
EBI remote Stage 2 is allowed.
```

Or run it directly:

```powershell
rbpfinder --genbank phage_X.gbk --phage-id X --out runs\X
```

## Two profiles

| Profile | You need | Sequences leave the machine? |
|---|---|---|
| **remote** (default) | this bundle only | yes — to EMBL-EBI, after an explicit disclosure and per-run authorization |
| **local** | this bundle + BLAST+ + a sequence database package | no |

The remote profile is the default deliberately: it means a new machine needs no multi-
hundred-megabyte database download. Choose **local** when the sequences are unpublished or
confidential — see `skill/rbpfinder/references/DATA_POLICY.md`.

Sequence databases are **not** in this bundle. They ship as separate, separately versioned
data packages, so the software and the data can be upgraded and audited independently.

## Scope and limits

```
Organisms   tailed dsDNA bacteriophages (Caudoviricetes) only
Platform    Windows validated; Linux and macOS NOT yet validated
Output      prioritised experimental candidates -- not validated biology
```

RBPfinder does not promise to find every real RBP in every phage. When the evidence is
insufficient it says `completeness: limited` and explains why, rather than padding the
list. Read that state; it is the honest part of the result.

See `skill/rbpfinder/references/KNOWN_ISSUES.md` for what v1 does not do.

## What is in here

```
runtime/{{WHEEL}}            the analysis software
runtime/wheels/                          dependencies for offline install, when this
                                          build has them bundled (see VERSION.json's
                                          bundled_dependency_wheels)
skill/rbpfinder/SKILL.md                 how an agent should drive it
skill/rbpfinder/references/              user guide, output guide, data policy, known issues
agents/AGENTS.md, agents/CLAUDE.md       short project-level instructions
install/                                 installer and verifier
VERSION.json                             machine-readable build facts
SHA256SUMS.txt                           checksum of every file above
```

Verify what you received:

```powershell
Get-FileHash -Algorithm SHA256 runtime\{{WHEEL}}
```

and compare against `SHA256SUMS.txt`.
