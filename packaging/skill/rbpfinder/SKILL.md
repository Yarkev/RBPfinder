---
name: rbpfinder
description: Prioritize receptor-binding protein candidates from tailed dsDNA bacteriophage genomes using the installed RBPfinder CLI, on a Windows or desktop machine. Use when analyzing an annotated phage GenBank file for candidate RBPs. For a Linux server, where local Foldseek structural evidence is reachable inside the run, use the Linux package instead.
---

# RBPfinder

Use the installed `rbpfinder` executable. **Do not reproduce or replace its scientific
decision rules in model reasoning.** The rules live in the package's frozen
`decision_rules.yaml`; anything you infer about tiers, direction, Primary/Rescue or
completeness that did not come out of a run is a guess wearing the tool's authority.

## Scope

RBPfinder v1.x is validated for tailed dsDNA bacteriophages (Caudoviricetes) only.

Do not claim validated support for RNA phages, ssDNA phages, filamentous phages,
archaeal viruses, or tailless phages unless a later release explicitly adds them. If the
user brings one of those, say so before running rather than after.

## Which skill package this is

This is the **desktop / Windows** package. There is a second package for Linux servers
(`packaging/biomni/skill/rbpfinder/`) and it is not interchangeable with this one: the two
platforms differ in *which evidence families are reachable*, which changes what you should
tell the user a run can and cannot establish.

| | Windows (this package) | Linux server |
|---|---|---|
| Family S, sequence homology | local BLAST database, if the user installs one | same |
| Family T, structure | **no native Foldseek build exists** — manual web route or import only | local Foldseek + ProstT5, inside the run |
| Remote submission | refused (see below) | refused |

Platform validation: **Linux is the validated route** as of v1.2.1 — it was verified
end-to-end against real Foldseek, a real structure database and a real BLAST database.
Windows runs, and was validated for earlier releases, but family T is not reachable inside
a run there, so a Windows run reaches a lower ceiling on the same genome. Say that before
running, not after. macOS is not validated.

## Before analysis

1. Run `rbpfinder doctor`. It only reports; it changes nothing, downloads nothing, and
   builds nothing.
2. Verify the input is an **annotated** GenBank file. pharokka output and any other
   annotated GenBank or RefSeq record are accepted; the hard requirements are CDS
   features carrying `/translation` and some form of `/locus_tag`. A tag that does not
   end in `CDS_<number>` is fine -- RBPfinder numbers those in genome coordinate order,
   preserves the original in `raw_locus_tag`, and says so. **Artefacts must not be mixed
   across annotation sources**: re-annotating a genome changes the CDS set, so ids from
   a pharokka run and ids from a RefSeq run are not interchangeable — RBPfinder needs CDS features with
   translations, not a bare nucleotide sequence. Do not eyeball it; run
   `rbpfinder validate-input --genbank <file>`. It takes seconds, changes nothing, uses
   the same readers the analysis uses, and names exactly what is wrong. Run it *before*
   any expensive annotation step too (phold is an hour or more on CPU), and before
   starting an analysis you would otherwise discover is impossible at the end.
3. Establish where Stage 2 evidence will come from. There are exactly two routes, and
   **neither transmits anything**:
   - `--stage2-backend local` (the default) searches configured BLAST databases. With no
     database configured, family S is simply absent and the run says so.
   - `--data-policy import_only` parses results the user obtained elsewhere by hand.

   Remote submission does not exist in this codebase: `--allow-remote`,
   `--data-policy remote_allowed` and `--stage2-backend ebi` are each refused with exit
   code 2, before any ingest. Never offer them, never describe an EBI route as available,
   and never ask the user for a contact email for one. See `references/DATA_POLICY.md`.
4. If family S matters for this genome and no database is configured, say so **before**
   the run rather than explaining a thin result afterwards. `rbpfinder doctor` reports it.

## Run

```
rbpfinder --genbank <file> --phage-id <id> --out <output-dir>
```

Useful additions:

- `--foldseek-db pdb100=<path> --prostt5-model <dir>` (Linux) — run **local Foldseek**
  structure search, family T, on this machine. This is the highest-value option for
  accuracy: the T family is what lifts a real RBP to R1 and what separates a binder from
  an apparatus protein, and locally it costs no tokens and transmits nothing (ProstT5
  predicts the structure from sequence; nothing is uploaded). Needs the `foldseek`
  executable (`--foldseek` or `$RBPFINDER_FOLDSEEK`), a structure database, and the
  ProstT5 weights — run `rbpfinder doctor` and confirm `Local Stage 3 AVAILABLE` first.
  Foldseek has no native Windows build, so this is Linux-only; on Windows, structural
  evidence stays on the manual web route. See `references/USER_GUIDE.md` §4a.
- `--host "<Genus species>"` — the bacterial host. A configured host database is used
  only when it covers this organism; otherwise it is skipped and the reason recorded.
- `--host-taxid <id>` — required for a remote host search. RBPfinder never guesses a
  taxonomy id, and neither should you.
- ~~`--stage2-backend ebi ... --allow-remote`~~ — **removed from the tool.** Every
  remote submission path is refused with exit code 2, on every platform, before any
  sequence is read. Nothing leaves the machine. The `--help` text for
  `--stage2-backend` still lists `ebi` as a choice; it parses and is then refused, so do
  not read that help text as a capability. Use the local backend, or
  `--data-policy import_only` to parse results obtained elsewhere.

  On Windows this does cost something real, and you should be straight with the user about
  it: without a local BLAST database there is no family S, and family T needs the manual
  web route either way. That is a lower evidence ceiling than a Linux server reaches.

Write every run into a **new** output directory. Never overwrite an input file.

## Interpret output

Report in this order, and use these names:

1. **Primary** — the candidates to test first.
2. **Rescue** — kept to protect recall, not because the evidence is strong.
3. **Completeness** — `complete` / `partial` / `limited`, plus the stated reason.
4. Exploratory or top unresolved candidates — only when the run actually surfaces them.

Never present the internal candidate pool as the experimental shortlist. It is a
recall-protection device sized for the algorithm, not for a bench.

**Do not expand Rescue to make recall look better.** When the evidence is insufficient the
correct output is `completeness: limited` with the reason — an honest short list beats a
padded one, and padding it silently destroys the only signal the user has about how much
to trust the run.

## Confirming a candidate, not just tiering it

A single run tells you what is worth testing. It does not, by itself, tell you what is
independently corroborated — that usually needs HHpred, AlphaFold or Foldseek results a
person has to submit by hand, since those default to manual handoff.

**On Windows there is no way around this for structure.** Foldseek has no native Windows
build, so family T cannot be produced inside a run here; a Linux server can, which is the
single biggest difference between the two packages. Do not quietly let a Windows run's
missing family T read as "no structural support" — it is "not looked for on this machine".

When the user wants a Primary or Rescue candidate actually resolved — not just left at its
tier — follow `references/INTERACTIVE_CONFIRMATION.md`: propose what's missing from the
batch files `rbpfinder run` already wrote, get the raw result back, reclassify with
`--stage2-manifest`, and repeat until the candidate set is resolved or nothing further is
findable.

Getting the result back is **manual by default**. The alternative is *you* driving the
third-party web service in a browser on explicit request, which is expensive in tokens and
needs a cost warning first — it is not RBPfinder transmitting anything, and it is not the
removed remote path coming back. Do not skip straight to it because it seems faster.

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

Before reporting results:

- Confirm the run exited 0 and cite the generated `run_result.json` and report files.
- Quote the completeness state as produced. **Do not claim biological completeness when
  RBPfinder reports `partial` or `limited`.**
- If a command failed, diagnose it as a capability or input error. Exit codes: `0` success,
  `2` input error, `3` missing capability, `1` unexpected internal error.
- **Never edit `decision_rules.yaml` to make a run succeed or a result look better.** A
  threshold changed to fit an answer is no longer evidence of anything.

## References

- `references/USER_GUIDE.md` — installation, running, reading the output
- `references/OUTPUT_GUIDE.md` — what each output file and field means
- `references/DATA_POLICY.md` — what leaves the machine, when, and under what authorization
- `references/KNOWN_ISSUES.md` — what v1 does not do, and where it is known to be weak
- `references/INTERACTIVE_CONFIRMATION.md` — the propose → external-evidence →
  reclassify → converge loop for resolving a candidate beyond its automated tier
