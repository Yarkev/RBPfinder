---
name: rbpfinder
description: Prioritize receptor-binding protein (RBP) candidates in tailed dsDNA bacteriophage genomes using the installed RBPfinder CLI. Use when analysing a phage genome for candidate RBPs, tailspikes, tail fibres or depolymerases. Requires an annotated GenBank; this skill does not annotate genomes.
---

# RBPfinder (Linux build)

Run the installed `rbpfinder` executable. **Do not reproduce or replace its scientific
decision rules in your own reasoning.** The rules live in the package's frozen
`decision_rules.yaml`; anything you assert about tiers, direction, Primary/Rescue or
completeness that did not come out of a run is a guess wearing the tool's authority.

## Division of labour

    annotation   you run pharokka (and phold only if asked)   <- your job
    judgment     rbpfinder                                     <- the only thing that
                                                                  decides what is an RBP

RBPfinder deliberately does not annotate genomes. Keeping the annotator outside it means
a pharokka or PHROGs upgrade cannot quietly move a tier, and the judgment layer stays
frozen, auditable and reproducible.

Do not do the judgment step yourself by reading evidence and ranking candidates. That is
where naive pipelines fail, in three specific ways this tool exists to prevent:

- **Correlated evidence counted as independent votes.** A PHROG keyword, a phold top hit
  and a structure-database hit are frequently the same fact seen three times. "Three
  evidence chains agree" is not three votes. RBPfinder de-correlates into evidence
  families and counts families, never tools.
- **Apparatus mistaken for binder.** "Tail fibre assembly protein" is a chaperone. A
  baseplate or an Ig-like adapter rod sits in the tail without binding anything.
- **Always producing a confident answer.** Sometimes the evidence cannot narrow the
  candidates. RBPfinder then reports `completeness: limited` and says why. That state is
  the most useful thing in the output.

## Scope

Validated for tailed dsDNA bacteriophages (*Caudoviricetes*) only. Do not claim validated
support for RNA phages, ssDNA phages, filamentous phages, archaeal viruses or tailless
phages. If the user brings one, say so before running rather than after.

**This build has no remote path.** `--allow-remote`, `--data-policy remote_allowed` and
`--stage2-backend ebi` are refused with exit code 2. Sequences never leave the machine.
Nothing is given up: on this server every evidence family is reachable locally.

## Procedure

### 1. Settle the route with the user

RBPfinder needs an annotated genome, and the two routes differ by about an hour of CPU,
so the choice is the user's. Ask before annotating:

- **fast (default)** — pharokka, then RBPfinder with local Foldseek structure search over
  the candidate proteins. Minutes. Structural evidence is still used.
- **accurate** — additionally runs phold genome-wide first, which resolves proteins
  pharokka leaves as "hypothetical" and so improves nomination. 1–2 h extra on CPU.

If the user has no preference, use **fast**. Prefer accurate when the genome is poorly
annotated, or when a fast run came back `limited`.

phold does **not** add a second structural evidence family — de-correlation collapses a
phold hit and a Foldseek hit to one family by design. What it buys is annotation quality.

### 2. Annotate (your job, not the tool's)

```
pharokka.py -i <genome.fasta> -o <pharokka_out> -d <PHROGS_DB> -t <threads> -f
# accurate route only:
phold run -i <pharokka_out>/pharokka.gbk -o <phold_out> -t <threads>
```

Verify the flags against the installed version (`pharokka.py --help`, `phold run --help`);
they move between releases. What is fixed is the deliverable.

Hand over **phold's GenBank** (`phold.gbk`), never its per-CDS predictions TSV — the
table carries no protein sequence, so local structure search would be silently
unavailable.

### 3. Validate before analysing

```
rbpfinder validate-input --genbank <file>        # add --json for machine output
```

Seconds, changes nothing, uses the same readers the analysis uses. On the accurate route
run it on the pharokka GenBank *before* committing an hour to phold. If it reports
`NOT_USABLE`, report the stated problem to the user and stop — do not proceed.

Input requirements: CDS features with `/translation`, and some form of `/locus_tag`.
pharokka output and NCBI RefSeq records are both accepted. A tag that does not end in
`CDS_<number>` is fine — those CDS are numbered in genome coordinate order, the original
tag is kept in `raw_locus_tag`, and each assignment is reported as a warning.

**Do not mix artefacts across annotation sources.** Re-annotating a genome changes the
CDS set, so ids from a pharokka run and ids from a RefSeq run are not interchangeable.

### 4. Run

```
rbpfinder --genbank <file> --phage-id <id> --out <new-output-dir>
```

Useful additions:

- `--host "<Genus species>"` — the bacterial host. A configured host database is used
  only when it covers this organism; otherwise it is skipped and the reason recorded.
  Give it when known: it materially improves the result.
- `--foldseek-db pdb100=<path> --prostt5-model <dir>` — **local Foldseek structure
  search** (family T). This is the highest-value option for accuracy: family T is what
  lifts a real RBP to the top tier and separates a binder from an apparatus protein.
  ProstT5 predicts structure from sequence, so there is no AlphaFold step and nothing is
  transmitted. Usually preset via `$RBPFINDER_FOLDSEEK_DB` / `$RBPFINDER_PROSTT5_MODEL`;
  confirm with `rbpfinder doctor` that `Local Stage 3` is AVAILABLE.
- `--circular` — explicit override only. A GenBank declaring `topology: circular` is
  detected automatically; pass this only for a circular genome whose record omits the
  annotation.

Write every run into a **new** output directory. Never overwrite an input file.

### 5. Report

In this order, and with these names:

1. **Primary** — the candidates to test first.
2. **Rescue** — kept to protect recall, not because the evidence is strong.
3. **Completeness** — `complete` / `partial` / `limited`, plus the stated reason.
4. Exploratory or top unresolved candidates — only when the run actually surfaces them.

Quote tiers as produced (e.g. `R1-E3`). Do not re-rank and do not add candidates.
`all_CDS_screening.tsv` answers "why was this CDS not a candidate?" — cite it when the
user asks about a protein that did not make the list.

**Do not expand Rescue to make recall look better.** When the evidence is insufficient
the correct output is `completeness: limited` with the reason. Padding it destroys the
only signal the user has about how much to trust the run.

Never present a `partial` or `limited` run as biological completeness.

## Scientific invariants

Preserve these when summarising. They are the distinctions the tool exists to make:

- **searched-no-hit is not not-run.** "We looked and found nothing" and "we never looked"
  are different states and RBPfinder records them differently.
- **Absence of evidence is not negative evidence.** A search that found nothing never
  lowers a candidate's tier.
- **One sequence-homology family stays one S**, however many databases were searched.
  Do not describe two databases as two independent lines of evidence.
- **Completeness limitations must be reported explicitly**, never smoothed over.
- A compound product name takes the conservative reading — but an explicit functional
  claim ("receptor binding protein", "adsorption protein") is not overridden by a
  structural word elsewhere in the name.

## Completion checks

- Confirm the run exited 0 and cite `run_result.json` and the report files.
- Quote the completeness state as produced.
- Exit codes: `0` success, `2` input error, `3` missing capability, `1` unexpected
  internal error. A `2` means the user's input or flags need fixing — say what and why,
  and do not retry unchanged.
- **Never edit `decision_rules.yaml` to make a run succeed or a result look better.** A
  threshold changed to fit an answer is no longer evidence of anything.

## References

- `KNOW_HOW.md` — the procedure and the failure modes a naive pipeline reproduces
