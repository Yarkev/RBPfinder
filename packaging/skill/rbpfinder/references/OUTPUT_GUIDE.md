# What RBPfinder writes, and what each thing means

Every run writes into the directory given by `--out`. Nothing is written anywhere else,
and the input file is never modified.

## The files

| File | What it is | Who should read it |
|---|---|---|
| `RUN_REPORT.md` | The human-facing report: Primary, Rescue, Completeness, in that order | start here |
| `RBP_candidates.tsv` | The shortlist as a table, one row per candidate | bench planning |
| `run_result.json` | The structured result, including provenance and every state the run recorded | agents, scripts |
| `all_CDS_screening.tsv` | Every CDS the run considered, with its tier and evidence families | audit, "why was X not listed" |
| `STAGE2_REQUIRED.tsv` | Candidates still owed a sequence search. **A header-only file means nothing is owed** | diagnosing an incomplete run |
| `stage2_homolog_consensus.json` | The per-hit consensus behind each S record | checking a specific call |
| `AF3_MONOMER_BATCH.tsv` | Structure-prediction priority list, if applicable | optional follow-up |
| `HHPRED_BATCH.tsv` | Profile-search batch, if applicable | optional follow-up |

## The three layers, in the order they must be reported

**Primary** — test these first. Evidence points at receptor binding from at least one
direct family (sequence, domain or structure), usually with a second independent family
agreeing.

**Rescue** — kept so a real RBP is not lost, *not* because the evidence is good. Presenting
Rescue as though it were Primary misrepresents the run. Presenting it as noise is also
wrong: it exists because the cost of missing an RBP is higher than the cost of testing one
extra protein.

**Completeness** — one of:

- `complete` — the evidence was sufficient to compress the candidate space
- `partial` — some region or locus remains unresolved; the report names it
- `limited` — the evidence was not sufficient; the report says why

`partial` and `limited` are **results**, not failures, and must be reported verbatim. A run
that says `limited` and explains itself is more useful than a run that pads Rescue until
the recall number looks good.

## Where the evidence sits: two different columns

`RBP_candidates.tsv` carries two things that look similar and answer different questions.

| Column | Question it answers |
|---|---|
| `region_start_aa` / `region_end_aa` | which part of the CDS this candidate row is about |
| `rbp_evidence_localisation` | where on the protein the receptor-binding evidence actually falls |

`rbp_evidence_localisation` is a **list of residue spans, not a range**:

```
aa 111-667                 one span carries the evidence
aa 111-667, 116-665        two spans do; both are reported
(empty)                    no span carries it -- see below
```

Read the commas. A span list is not an interval: `aa 10-50, 200-250` means residues 10-50
and 200-250, and says nothing about 51-199. Earlier versions reported a single enclosing
range instead, which asserted that everything between the outermost spans was evidence.
**If you have a script that parses this column with a single `aa <start>-<end>` pattern,
split on commas first** -- otherwise it will silently keep only the first span.

An empty value where the layer is `RBP_core` means the evidence supports the call but no
span could place it -- typically because the only matches covered the whole protein, which
identifies it without localising anything. That is reported as "unresolved", never as
"the whole protein".

## The internal candidate pool is not the shortlist

`all_CDS_screening.tsv` contains a much larger internal pool. It is sized to protect recall
inside the algorithm, not to be handed to anyone with a pipette. Do not quote its size as
"the candidates".

## Two empty states that are not the same

This distinction runs through the whole output and is easy to flatten:

```
searched, found nothing   ->  an empty result file, not_run = false
never searched            ->  not_run = true, and STAGE2_REQUIRED lists it
```

An empty artefact on disk is a *state*, not a missing one. A header-only
`STAGE2_REQUIRED.tsv` means "nothing is owed", not "this step never ran".

## Provenance in `run_result.json`

Worth quoting when a user asks how a result was reached:

- `stage2_local` / `stage2_remote` — which backend ran, which databases were used, and
  which were **skipped and why** (a host database that does not cover the stated host is
  skipped deliberately, and the reason is recorded)
- `stage2_remote.disclosure` — what the user was told before any sequence was transmitted
- per-taxonomy state — whether a host search was `searched`, `not_run`, or
  `not_applicable`; these are three different things

## Exit codes

```
0  success
2  input error          bad GenBank, missing required argument, missing EBI email
3  capability missing   no blastp, no configured database, remote not authorized
1  unexpected internal error  -- this one is a defect, report it with the traceback
```
