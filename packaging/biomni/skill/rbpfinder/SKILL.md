---
id: "skill_a75c124d884248a6a599dad14c7f4b24"
name: "rbpfinder"
description: "Prioritize receptor-binding protein (RBP) candidates from tailed dsDNA bacteriophage genomes using the installed RBPfinder CLI. Use when analyzing an annotated phage GenBank file for candidate RBPs, tailspikes, tail fibres or depolymerases. Linux build: fully offline, sequences never leave the machine."
category: "general"
visibility: "public"
starting-prompt: "Analyse this phage GenBank file with RBPfinder and tell me which proteins to test first as receptor-binding proteins."
---

# RBPfinder (linux/biomni build, v1.2.4+linux)

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

## Which skill package this is

This is the **Linux server** package, and the one that reaches the full evidence set: it
is the only platform where family T, structural evidence, is produced *inside* a run. A
second package (`packaging/skill/rbpfinder/`) covers Windows and desktop machines, where
Foldseek has no native build and family T needs the manual web route. The two are not
interchangeable — what you can honestly tell a user a run established differs between
them.

## Offline by design

All remote submission paths are **removed**: EBI BLAST, remote InterProScan, HHpred web,
Foldseek web, AlphaFold Server, cloud annotation and remote UniProt enrichment do not
exist. `--allow-remote`, `--data-policy remote_allowed` and `--stage2-backend ebi` are
refused with exit code 2, before any ingest. Never suggest them, never attempt them,
never transmit a user sequence anywhere.

The refusal is **not** a property of this build — it is unconditional in the source, so
there is no other build to send a user to for a remote route. `--help` still lists `ebi`
as a `--stage2-backend` choice; it parses and is then refused, so do not read the help
text as a capability.

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

**Say what accurate buys before they decide, because it is larger than it sounds.** On T7
it is the difference between the literature-established RBP being in the shortlist and
not being in it:

```
fast      Primary: CDS_0039, CDS_0040              gp17  R3-E3   not in Primary
accurate  Primary: CDS_0039, CDS_0040, CDS_0052    gp17  R2-E3   in Primary
```

No sequence database closes that gap — see Bootstrap. If the user's genome is one where
getting the real RBP into the shortlist matters more than an hour of CPU, accurate is the
answer, and an hour is cheap against a wrong shortlist.

What phold buys is **annotation quality**: it resolves CDS pharokka leaves as
"hypothetical", which improves nomination and the direction read off a product name. It
does **not** add a second independent structural evidence family — de-correlation
collapses a phold hit and a Foldseek hit to one family by design.

Hand over phold's **GenBank** (`phold.gbk`) with `--genbank`. That is the recommended
route and the one every measurement here used.

The per-CDS predictions TSV has no protein-sequence column, so on its own it cannot
produce family T for anything. That used to fail silently — `cds_searched: 0` with no
warning, measured on a real genome where it cost two true candidates. It now **refuses**
with exit 2 when a structure database is configured. If you have a reason to want the
TSV's extra per-CDS fields (`prostt5_confidence`, `qCov`, the category proportions),
pass both:

```bash
rbpfinder --phold phold_per_cds_predictions.tsv --phold-genbank phold.gbk ...
```

CDS are matched on coordinates and a poor match is refused rather than guessed at: a
sequence attached to the wrong CDS would be a confident wrong answer that nothing
downstream could detect.

## Bootstrap: making the local capabilities available

`doctor` reporting UNAVAILABLE is the start of a task, not the end of one.

**Local Stage 2 (family S).** Configure a sequence database:
`rbpfinder database configure phage <blast-db-prefix>` (and `host` if you have a matching
one). Without it the S family does not exist at all.

**But the database's annotation quality decides whether it changes any conclusion, and
you must not promise otherwise.** Measured on T7 with a full RefSeq viral protein
database (722,823 sequences): gp17's top-10 homologs are *all* annotated "tail fiber
protein", so the S record reads apparatus, 10 hits out of 10. gp17 went `R3-E1` →
`R3-E3` and stayed out of Primary. **More families raise `tier_e`; only a
receptor-binding direction raises `tier_r`.**

So tell the user what a database can and cannot do: a library of part names improves how
well the genome was looked at. What lifts a candidate is a *functional* term — "receptor
binding", "adsorption", "depolymerase" — in a homolog annotation, a PHROG annotation or
a curated entry. On T7 the CDS that did reach Primary (CDS_0039/0040) got there on
"adsorption" in their homolog annotations.

On a sandbox whose home directory is not in the persistent snapshot, `source` the
environment file first: it sets `RBPFINDER_CONFIG` to a path that survives, so the
registration is not lost on resume. If `doctor` reports `DB_PHAGE NOT CONFIGURED` on a
machine where you know you configured it, that is the first thing to check — the database
files usually survived and only the registry entry was lost.

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
- `--foldseek-all-cds` — **experimental.** Searches every CDS instead of the nominated
  pool, which makes the **A2 completeness audit runnable** (A2 asks a genome-wide
  question, so a pool-scoped search cannot answer it). On the default path A2 reports
  itself unexecutable, and that is the original design behaving correctly, not a defect
  to work around.

  **It is not a no-op on the result.** A2 reopens the pool, reopened CDS are classified
  like any other, so new candidates can appear and the Primary top-up rule can admit
  them — measured on one strain where Primary went from 1 candidate to 3. What it does
  *not* change is the tier a CDS earns from a given evidence set, and the reopen
  provenance itself contributes no evidence family.

  Pilot benchmark on 6 labelled strains: `false-complete` 0 in 12 of 12 runs,
  complete-recall@K identical in both arms, 362 reopened-and-downgraded against 2
  reopened-and-promoted. The full 48-strain regression has not been run. Say which path
  you used when you report, because the completeness state depends on it.
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
literature-established RBP, came out `R3 / unresolved / supports_apparatus_membership`
with a strong structural hit, because every PDB title for it is a part name or a
crystallographic phrase.

Nor does adding a sequence database fix it by itself — see Bootstrap. gp17 sat at `R3`
with `G+S+T`, three families, all three reading apparatus.

**What moved it was annotation, not search.** Running phold gives gp17 a `phrog:4242`
annotation, which produces a **D family** record; the D term map reads "tail fiber
protein" as `supports_receptor_binding`, and that single direct family takes gp17 to
`R2-E3` and into Primary. Measured both ways on T7:

```
fast     RefSeq GenBank + S + T   Primary: CDS_0039, CDS_0040        gp17  R3-E3
accurate phold.gbk       + S + T   Primary: + CDS_0052 (gp17)        gp17  R2-E3
```

So when the user wants better answers on a poorly-annotated genome, **the accurate route
(phold) does more than any database or flag.** Say that rather than sending them to
configure another library.

### When the user has a structure, the structure outranks the annotation

If the user brings a cryo-EM map, a published structure, or a high-confidence predicted
model, **structural localisation beats annotation transfer** — and you should say so
plainly rather than defending the tool's ranking.

Measured against a user's cryo-EM map on a *Salmonella* phage, 5 candidates:

```
4  structurally supported          -- including the top RBP, a 986 aa fibre with a
                                      Family 11 sugar-binding module
1  relocated, not wrong            -- a capsid decoration protein with Ig domains; not a
                                      tail RBP, but its sugar-binding ability is worth
                                      testing on its own
1  FALSE POSITIVE                  -- annotated "tail fiber protein"; the map shows a
                                      tube initiator, a baseplate assembly component
```

The false positive is instructive and you should expect its shape: the protein was pushed
into Primary by a part-name annotation agreeing with itself across D and S (a 99.3%
identity homolog also called "tail fiber protein"). **Agreement between two readers of
the same part name is not two independent lines of evidence about function** — it is the
same annotation seen twice. De-correlation collapses tools, not vocabularies.

`presentation_signals` in `run_result.json` now flags that shape without a structure in
hand: it reports when a hit *description* describes baseplate or assembly machinery while
the product name claims a fibre, and when a fibre assembly chaperone sits immediately
downstream (the measured true fibre had one; the false positive did not). Those are hints
for a reader and nothing more — they changed no tier.

So when structural information exists: report the tool's ranking as produced, then state
where the structure disagrees and which way. Do not re-rank, and do not argue the tier.

**This is one genome.** 4-of-5 is not a measured accuracy and the shapes above are not a
checklist to match against — one *Salmonella* myovirus is where they were seen, and the
presentation signals' vocabulary was built from the same genome. Treat them as things
worth looking for, not as what you should expect to find. If a run's signals fire on
nothing, that is as likely to mean the vocabulary does not cover this phage's anatomy as
that the candidates are clean — the terms are myovirus tail anatomy, and T7, a podovirus
with no baseplate, fires none of them.

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
- **Read `presentation_signals`, and report it as what it is.** Three checks run after
  the candidate sets are fixed and flag shapes worth a human look: a product name
  claiming a fibre while a hit *description* describes baseplate machinery; a fibre
  assembly chaperone immediately downstream; the candidate's own records disagreeing
  about direction. **No signal there changed a tier, a family or a set, and none may be
  reported as evidence that a protein is or is not an RBP.** Say "this is flagged as
  worth checking", never "the tool thinks this is not an RBP". Its vocabulary is
  `calibration: pending`, so if `term_trigger_counts` shows a term firing on nearly
  every candidate, report that too — it means the hint is noise, not that the genome is
  unusual. And **a signal firing on nothing is not a clean bill of health**: the
  vocabulary is myovirus tail anatomy derived from a single genome, so on a siphovirus or
  podovirus a zero means "not applicable" rather than "nothing to flag".
- **`top_unresolved_candidates` is a conditional key.** It appears only when the
  shortlist is empty, or when `completeness` is `limited`, so the top-level key count is
  31 or 32. It is presentation-only: those CDS are **not** promoted to Rescue and are
  **not** counted in any recall figure. Do not read its absence as a signal — it means
  the shortlist was non-empty and not `limited`, which `primary_set`, `rescue_set` and
  `completeness_assessment` already told you.
- **Quote `versions` when you report a number.** Every run records
  `versions: {package, rules, schema}` and `vocabulary: {mode, overridden}`. A tier, a
  rank or a completeness state is only meaningful against the rules that produced it, so
  a result reported without them cannot be compared with a later one. If `package` reads
  `0+unknown`, the tool is running from a source tree that was never installed — say so,
  because that is not a released version.
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
| `A2 could not run: ...missing_genomewide_T_or_M` | the structure search was pool-scoped, or no structure source exists | **state the completeness ceiling** — this is the designed behaviour on the default path, not a fault. `--foldseek-all-cds` makes A2 runnable — experimental, and not a no-op on the result; see its entry above |
| exit 2 mentioning schema validation | internal contract violation | report it; the user's input is not at fault |
| a structure database label is refused | the label is not one Stage 3 recognises | use `pdb100` / `cath50` |
| `stage3_local.cds_searched: 0` on a Mode B run | the phold TSV carries no protein sequences | this now refuses with exit 2 instead. Use `--genbank phold.gbk`, or add `--phold-genbank phold.gbk` |
| `Cannot open lookup file ..._ca.lookup` after `foldseek databases PDB` | the output prefix contained a directory component, so foldseek's own install script failed its `mv` and scattered the files | re-download from **inside** the target directory with a flat prefix: `cd /path/to/dbs && foldseek databases PDB pdb100 tmp` |
| `foldseek databases` complains about arguments | it needs **three** paths: `databases <name> <out-prefix> <tmp>` | pass all three |
| the foldseek download lands on a repository page | `mmseqs.org` 302s to the repo | use `https://mmseqs.com/foldseek/foldseek-linux-avx2.tar.gz` |
| `pharokka install` reports an MD5 mismatch | Zenodo returns **403** to data-centre IPs, and the "database" downloaded is a few-hundred-byte block page | check the file size before believing the MD5 message. Install the database by hand: unpack the tarball and flatten the inner directory into `<env>/lib/python3.*/databases/`, then verify with `check_db_installation()` |
| `np.long` AttributeError from phold | mixed numpy/scipy versions | phold is not on bioconda: `pip install phold==1.2.5` with `numpy<2` and `transformers==4.46.3` pinned; repair with `pip install --force-reinstall --no-deps scipy==1.14.1` |
| `blastp` prints USAGE | the flag is `-num_threads`, not `-threads` | — |
| a tool that worked yesterday is gone | `/opt/conda` and apt-installed packages do not survive hibernation | put every prefix environment under `/workspace` |
| a run behaves like the OLD code after `git checkout` | a non-editable copy was installed into the venv and shadows the editable path, so `import rbpfinder` resolves to the copy | **verify, do not assume**: `python -c "import rbpfinder,inspect;print(inspect.getsourcefile(rbpfinder))"` must point into your checkout. `uv pip install -e` survives a branch switch with no reinstall; changing *install method* needs this check. Also compare `versions.package` in `run_result.json` against the commit you meant to test |
| NCBI RefSeq viral proteins 404 | the file is volumed: `viral.1.protein.faa.gz`, not `viral.protein.faa.gz` | — |

## Editing the rules

**Never edit `decision_rules.yaml` to make a run succeed or a result look better.** A
threshold changed to fit an answer is no longer evidence of anything.

That prohibition is about *results*. Repairing an executor contract that is demonstrably
wrong against an upstream tool — a column a real binary cannot produce, a pattern that
does not match a real database's identifiers — is legitimate, and must be recorded in
`CHANGES-linux.md` and reported to the user with the evidence.

### `--vocabulary` is for comparison, not for tuning

Three readers judge whether a description supports receptor binding, from three term maps
that do not fully agree. `--vocabulary {current,explicit_guard,unified}` selects which is
authoritative so the difference can be *measured*; `current` is the default and reproduces
the frozen behaviour exactly. The mode used is recorded in `run_result.json`, and results
from different modes are not comparable with each other.

Run the default unless the user asks for a comparison. Under `unified`, `tail fiber
protein` as a CDS's own product name reads as apparatus membership rather than receptor
binding, which changes tiers — so switching modes to make a particular candidate appear is
the same prohibited move as editing a threshold. `packaging/comparison/` holds the plan and
the harness.

## References

- `references/KNOW_HOW.md` — the procedure and the failure modes it prevents
