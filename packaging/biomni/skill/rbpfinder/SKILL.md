---
id: "skill_a75c124d884248a6a599dad14c7f4b24"
name: "rbpfinder"
description: "Prioritize receptor-binding protein (RBP) candidates from tailed dsDNA bacteriophage genomes using the installed RBPfinder CLI. Use when analyzing an annotated phage GenBank file for candidate RBPs, tailspikes, tail fibres or depolymerases. Linux build: fully offline, sequences never leave the machine."
category: "general"
visibility: "public"
starting-prompt: "Analyse this phage GenBank file with RBPfinder and tell me which proteins to test first as receptor-binding proteins."
---

# RBPfinder (linux/biomni build, v1.2.1+linux)

Use the installed `rbpfinder` executable. **Do not reproduce or replace its scientific
decision rules in model reasoning.** The rules live in the package's frozen
`decision_rules.yaml`; anything you infer about tiers, direction, Primary/Rescue or
completeness that did not come out of a run is a guess wearing the tool's authority.

## Division of labour

    annotation   you run pharokka (and phold only when asked)   <- your job
    judgment     rbpfinder                                       <- the only thing that
                                                                    decides what is an RBP

RBPfinder does not annotate genomes. Keeping the annotator outside it means a pharokka or
PHROGs upgrade cannot quietly move a tier.

Do not do the judgment step yourself by reading evidence and ranking candidates. Three
specific failures this tool exists to prevent:

- **Correlated evidence counted as independent votes.** A PHROG keyword, a phold top hit
  and a structure hit are frequently the same fact seen three times.
- **Apparatus mistaken for binder.** See "Reading direction labels" below for where this
  heuristic applies and where it does not.
- **Always producing a confident answer.** Sometimes the evidence cannot narrow the
  candidates. The tool then reports `completeness: limited` and says why.

## Scope

Validated for tailed dsDNA bacteriophages (Caudoviricetes) only. Do not claim validated
support for RNA phages, ssDNA phages, filamentous phages, archaeal viruses, or tailless
phages. If the user brings one of those, say so before running rather than after.

## This build is offline by design

All remote submission paths are **disabled**: EBI BLAST, remote InterProScan, HHpred web,
Foldseek web, AlphaFold Server, cloud annotation and remote UniProt enrichment do not
exist here. `--allow-remote`, `--data-policy remote_allowed` and `--stage2-backend ebi`
are refused with exit code 2. Never suggest them, never attempt them, never transmit a
user sequence anywhere.

Evidence comes from: a local BLAST database (**if one is configured**), a local Foldseek
install (**if one is configured**), results the user already has on disk (import mode), or
nothing — in which case the run honestly reports what it could not do. Do not tell the
user that everything is available locally; check `doctor` and say what actually is.

## Before analysis

1. Run `rbpfinder doctor`. It only reports; it changes nothing and downloads nothing.
   Read which capabilities are AVAILABLE — the answer changes what the run can conclude.
2. Run `rbpfinder validate-input --genbank <file.gbk>` (add `--json` for machine-readable
   output). Seconds, using the same readers the run uses, instead of a failed hour.
3. The input must be an **annotated** GenBank — CDS features with `/translation`.
   pharokka output and NCBI RefSeq/GenBank records are both accepted; locus tags in any
   format are fine, and non-`CDS_<n>` tags are numbered by genome-coordinate order with a
   warning. A bare nucleotide FASTA is not: the CLI prints how to annotate and exits.
   **Do not mix artefacts across annotation sources** — re-annotating changes the CDS set,
   so ids from a pharokka run and a RefSeq run are not interchangeable.
4. **Check topology.** Circularity is read from the record's own `topology`; `--circular`
   is an explicit override. If `validate-input` warns that no topology is declared and the
   genome is circular, pass `--circular`. This matters and is invisible otherwise: a tail
   module spanning the origin loses its C1/C3 nominations while the **pool size does not
   change**, because another channel recovers the same CDS, so no printed number reveals
   the degradation.

## Choosing the route — ask the user first

The two routes differ by about an hour of CPU, so the choice is the user's.

- **fast (default)** — pharokka, then RBPfinder. Minutes.
- **accurate** — additionally runs phold genome-wide first. 1–2 h extra on CPU.

If the user has no preference, use fast. Prefer accurate when the genome is poorly
annotated (many hypothetical proteins), or when a fast run came back `limited`.

What phold buys is **annotation quality**: it resolves CDS pharokka leaves as
"hypothetical", which improves nomination and the direction read off a product name. It
does **not** add a second independent structural evidence family — de-correlation
collapses a phold hit and a Foldseek hit to one family by design.

Hand over phold's **GenBank** (`phold.gbk`), never its per-CDS predictions TSV: the table
carries no protein sequence, so local structure search would be silently unavailable.

## Bootstrap: making the local capabilities available

`doctor` reporting UNAVAILABLE is the start of a task, not the end of one.

**Local Stage 2 (family S — the one that usually decides the answer).** Configure a
sequence database: `rbpfinder database configure phage <blast-db-prefix>` (and `host` if
you have a matching one). Without it the S family does not exist, and S is what carries
*functional* homolog annotations — the thing that lifts a candidate above apparatus.

**Local Stage 3 (family T — structure).**

```bash
# the foldseek binary (static, no root; the repository moved to steineggerlab)
# https://mmseqs.com/foldseek/foldseek-linux-avx2.tar.gz

foldseek databases ProstT5 <weights_dir> <tmp>    # ~2.3 GB, f16 gguf
foldseek databases PDB     <prefix>      <tmp>    # ~4.7 GB
```

- `<prefix>` is a **file prefix, not a directory**. Pass it as
  `--foldseek-db pdb100=<prefix>`.
- The label must be one Stage 3 recognises (`pdb100`, `cath50`, or an apparatus-only
  name); any other label is refused rather than searched and silently dropped.
- Use a database downloaded by `foldseek databases`. A database built by hand from PDB
  files has no taxonomy files, and the search dies at run time; `doctor` now reports such
  a database as `CONFIGURED, INVALID` rather than AVAILABLE.
- On this route `probability_pct` is always **null**. That is expected, not a failure: a
  ProstT5 query is a sequence with no backbone coordinates, so foldseek cannot compute a
  probability at all. Do not report it as a problem and do not retry.

## Run

```
rbpfinder --genbank <file.gbk> --phage-id <id> --out <output-dir>
```

Useful additions:

- `--host "<Genus species>"` — used **only if** a host database is configured and covers
  that organism; otherwise it is recorded and skipped. Do not promise the user
  host-aware filtering without checking `doctor` first.
- `--foldseek-db pdb100=<prefix> --prostt5-model <weights_dir>` — local structure search.
  Usually preset via `$RBPFINDER_FOLDSEEK_DB` / `$RBPFINDER_PROSTT5_MODEL`.
- `--foldseek-all-cds` — **experimental, benchmark pending.** Searches every CDS instead
  of the nominated pool, which makes the **A2 completeness audit runnable** (A2 asks a
  genome-wide question, so a pool-scoped search cannot answer it). Verified correct on a
  single genome; its effect on complete-recall and on the `false-complete = 0` invariant
  across the benchmark has not been measured. Use it to investigate, not as the default
  for a result someone will act on. The default pool-scoped search is the validated
  working state, and A2 reporting itself unexecutable on that path is the original
  design behaving correctly, not a defect to work around.
- `--comparative-cohort <dir-of-genbanks>` — the C5 comparative channel (needs blastp +
  makeblastdb).
- `--data-policy import_only` — parse existing results the user supplies; run no search.
- `--stage2-manifest <manifest>` — reclassify after external results come back.

Write every run into a **new** output directory. Never overwrite an input file.

## Interpret output

Report in this order, and use these names:

1. **Primary** — the candidates to test first.
2. **Rescue** — kept to protect recall, not because the evidence is strong.
3. **Completeness** — `complete` / `partial` / `limited`, plus the stated reason.
4. Exploratory or top unresolved candidates — only when the run surfaces them.

Never present the internal candidate pool as the experimental shortlist. It is a
recall-protection device sized for the algorithm, not for a bench.

**Do not expand Rescue to make recall look better.** When the evidence is insufficient the
correct output is `completeness: limited` with the reason.

### Reading direction labels

Every evidence record carries a direction. These appear in `run_result.json` and you will
be asked what they mean:

| Label | Means | Typical source |
|---|---|---|
| `supports_receptor_binding` | this evidence supports a direct RBP reading | a *functional* term: tailspike, adhesin, depolymerase, sialidase, "receptor binding" |
| `supports_apparatus_membership` | part of the tail apparatus; does not discriminate binding within it | a *part* name: "tail fiber protein", "distal tail", baseplate wedge |
| `supports_structural_only` | a structural component, not a binder | tail tube, sheath, connector, adaptor, "tail protein" |
| `uninformative` | no vocabulary matched | most PDB titles |

**`uninformative` in the majority is expected when searching PDB, and is not "no
evidence".** PDB titles are crystallographic descriptions ("Crystal structure of the
carboxy-terminal domain of …"), not functional annotations. A structural hit can be
excellent and still carry no functional word.

**Apparatus is not the same as "not an RBP".** The heuristic "apparatus ≠ binder" applies
to assembly proteins, chaperones, connectors and adaptors. It does **not** apply to a
single protein that is both — a tailspike is deliberately mapped to receptor binding,
because unlike "tail fiber protein" (an annotation the whole fibre cluster shares) it
names one protein that both sits in the tail and recognises the receptor. If a tailspike
reads as receptor binding, that is the design working, not a bug to question.

### When the shortlist is empty

Primary 0 does not mean "no RBP here". Before saying anything about the biology, check
which families could even run:

- `stage2_local` in `run_result.json` — was a sequence database configured?
- `stage3_local` — did local structure search run, and over what scope?
- the `limitations` list — which audits reported themselves unexecutable?

Then say which it is: **evidence starvation** (S and/or D never ran, so nothing could
reach the tiers that enter Primary) or **a genuinely unresolved genome** (everything ran
and still nothing qualified). Report the families that did not run alongside the result;
do not present a starved run as a biological finding.

### Family T alone is usually not enough

Local structure evidence rarely lifts a true RBP on its own. Measured on T7: gp17, the
literature-established RBP, came out `R3 / E2 / unresolved / supports_apparatus_membership`
with a strong structural hit, because every PDB title for it is a part name or a
crystallographic phrase. What drives a candidate up is the **S family** — homology to
characterised phage proteins whose annotations carry functional terms — with T
corroborating. If the user wants better answers, configuring a phage sequence database
does more than any flag.

## Known biology that disagrees with the output

When the user's genome contains a literature-established RBP and the run does not rank it
as one, **say so explicitly**. Pointing out a disagreement is not re-ranking.

State: (a) what the tool reported, quoted as produced; (b) what the literature says;
(c) the mechanism — usually part-name conservatism plus evidence starvation; and (d) that
you are not changing the tool's conclusion.

The worked example is T7 gp17 above. The correct remedy is to configure the missing
evidence source, not to argue the tier.

## Confirming a candidate, not just tiering it

A single run tells you what is worth testing with the evidence it could reach. Family T is
covered **inside the run** when local Foldseek is configured — do not send the user to an
external Foldseek or AlphaFold service for it.

The external loop remains for executors this build does not bundle: HHpred, InterProScan
and AlphaFold. Read the batch files the run wrote (`HHPRED_BATCH.tsv`,
`INTERPRO_BATCH.tsv`, `AF3_MONOMER_BATCH.tsv`), give them to the user to run, import the
returned raw results, reclassify with `--stage2-manifest`, and repeat until the candidate
set is resolved or nothing further is findable. The user submits; you never do.

## Scientific invariants

Preserve these when summarising:

- **searched-no-hit is not not-run.** The two states are recorded differently.
- **Absence of evidence is not negative evidence.** A search that found nothing never
  lowers a candidate's tier.
- **One sequence-homology family stays one S**, however many databases were searched.
- **Completeness limitations must be reported explicitly**, never smoothed over.
- A compound product name takes the conservative reading — but an explicit functional
  claim ("receptor binding protein", "adsorption protein") is not overridden by a
  structural word elsewhere in the name.

## Completion checks

- Confirm the run exited 0 and cite `run_result.json` and the report files.
- Quote the completeness state as produced. **Do not claim biological completeness when
  RBPfinder reports `partial` or `limited`.**
- Exit codes: `0` success, `2` validation failed, `3` missing capability, `1` unexpected
  internal error.
- **Read the message before assigning blame.** Exit 2 covers both a bad input and an
  internal schema violation; the schema case says so explicitly and means the tool
  produced a record its own contract forbids — report it, do not tell the user to edit a
  file that is fine.
- Exit 3 means a capability you asked for is not installed. Configure it (see Bootstrap)
  or fall back to `--data-policy import_only`. Do not retry the same command unchanged.

## Troubleshooting

| Symptom | Cause | What to do |
|---|---|---|
| easy-search dies naming a missing `_mapping` / `_nodes.dmp` / `_names.dmp` / `_merged.dmp` | the structure database has no taxonomy files | use one downloaded by `foldseek databases`; `doctor` flags this in advance |
| `probability_pct` is null everywhere | expected on the ProstT5 route — no backbone coordinates, so no probability exists | nothing; do not report it |
| `Local Stage 3 UNAVAILABLE` | foldseek, the structure database or the ProstT5 weights are missing | see Bootstrap |
| `A2 could not run: ...missing_genomewide_T_or_M` | the structure search was pool-scoped, or no structure source exists | **state the completeness ceiling** — this is the designed behaviour on the default path, not a fault. `--foldseek-all-cds` makes A2 runnable but is experimental and benchmark-pending |
| exit 2 mentioning schema validation | internal contract violation | report it; the user's input is not at fault |
| a structure database label is refused | the label is not one Stage 3 recognises | use `pdb100` / `cath50` |

## Editing the rules

**Never edit `decision_rules.yaml` to make a run succeed or a result look better.** A
threshold changed to fit an answer is no longer evidence of anything.

That prohibition is about *results*. Repairing an executor contract that is demonstrably
wrong against an upstream tool — a column a real binary cannot produce, a pattern that
does not match a real database's identifiers — is legitimate, and must be recorded in
`CHANGES-linux.md` and reported to the user with the evidence.

## References

- `references/KNOW_HOW.md` — the procedure and the failure modes it prevents
