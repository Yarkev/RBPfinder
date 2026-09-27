---
name: rbpfinder
description: Prioritize receptor-binding protein candidates from tailed dsDNA bacteriophage genomes using the installed RBPfinder CLI. Use when analyzing an annotated phage GenBank file for candidate RBPs.
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

Platform: **Windows is validated.** Linux and macOS are not yet validated — the
dependency wheels in this bundle are built for one platform and Python minor version.

## Before analysis

1. Run `rbpfinder doctor`. It only reports; it changes nothing, downloads nothing, and
   builds nothing.
2. Verify the input is an **annotated** GenBank file — RBPfinder needs CDS features with
   translations, not a bare nucleotide sequence.
3. Determine whether Stage 2 runs locally or at EBI (see `references/DATA_POLICY.md`).
4. For EBI remote Stage 2, before submitting anything:
   - state that **candidate protein sequences** and an **EBI contact email** will be
     transmitted to EMBL-EBI;
   - state that the email is EBI Job Dispatcher contact information — **not** an
     RBPfinder account, and **not** where results are delivered;
   - state that RBPfinder polls, downloads and parses the results automatically;
   - **never imply the user must fetch or return BLAST results manually**;
   - require explicit per-run authorization (`--allow-remote`).

   RBPfinder enforces all of this itself and refuses before transmission if the email or
   the authorization is missing. Your job is to make sure the user understands it, not to
   re-implement the check.

## Run

```
rbpfinder --genbank <file> --phage-id <id> --out <output-dir>
```

Useful additions:

- `--host "<Genus species>"` — the bacterial host. A configured host database is used
  only when it covers this organism; otherwise it is skipped and the reason recorded.
- `--host-taxid <id>` — required for a remote host search. RBPfinder never guesses a
  taxonomy id, and neither should you.
- `--stage2-backend ebi --data-policy remote_allowed --allow-remote --ebi-email <addr>`
  — remote Stage 2, with the disclosure above given first.

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
person has to submit by hand, since those default to manual handoff. When the user wants a
Primary or Rescue candidate actually resolved — not just left at its tier — follow
`references/INTERACTIVE_CONFIRMATION.md`: propose what's missing from the batch files
`rbpfinder run` already wrote, get the raw result back (manual by default; automated only
on explicit request, and never without a token-cost warning first), reclassify with
`--stage2-manifest`, and repeat until the candidate set is resolved or nothing further is
findable. Do not skip straight to the automated path because it seems faster.

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
