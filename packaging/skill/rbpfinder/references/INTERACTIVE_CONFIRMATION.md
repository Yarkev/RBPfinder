# Confirming a candidate — the interactive loop

A single `rbpfinder run` gives you Primary, Rescue and Completeness. It does not tell you
which candidate is *confirmed*: independently corroborated by two strong, direct lines of
evidence, the way a real RBP identification actually gets settled. That takes evidence
`rbpfinder run` alone usually does not have, because HHpred, AlphaFold and Foldseek default
to `MANUAL_HANDOFF` — nobody submits anything until a person does it.

This is how to run that confirmation, and how the assistant should drive it.

**A note on where "confirmed" comes from right now.** `rules/decision_rules.yaml` names
this bar precisely — `confirmed_rbp_designation`: tier R1, with at least two independent
direct families (a subset of sequence homology / domain profile / structure) each
individually reaching `strong`, not merely R1's own weaker "one direct family, any
strength" floor. As of this writing that designation is **SHADOW** (`live: false`) — it is
not yet a field `run_result.json` emits, because on every purely-automated recorded run
measured so far, nothing has independently reached it: the manual-mode families it needs
were never actually submitted. This loop is what closes that gap. Until it is adopted,
apply the bar yourself from what a reclassified run shows (`tier_r`, `tier_e`, and each
independent family's `strength` in `all_CDS_screening.tsv` or `run_result.json`) and say so
plainly — "two independent strong families, so I'd call this confirmed" — rather than
quoting a field that does not exist yet.

## When to use this

After a normal run, when Primary or Rescue holds a candidate the user wants resolved rather
than left at its tier — not every run needs this, and a user who just wants the automated
shortlist should get exactly that, unchanged.

## The loop

```
1. propose   which candidates are unresolved, and what would resolve each
2. request   hand the user (or, on request, fetch) the raw evidence
3. interpret read what came back, not just its headline score
4. reclassify feed it back through the same run, same output directory
5. converge  stop when resolved, or when nothing further is findable
```

### 1 — Propose

Read `run_result.json`. For every Primary/Rescue candidate that does not yet meet the
confirmed bar above, `rbpfinder run` has already written a batch file for whatever evidence
would settle it —
`HHPRED_BATCH.tsv`, `FOLDSEEK_BATCH.tsv`, `AF3_MONOMER_BATCH.tsv`, `INTERPRO_BATCH.tsv` — and
each row carries `why_requested`: which family is missing and what running this analysis
would decide. **Read that field; do not invent your own reasoning.** If a batch file is
absent or empty for a candidate, that family already has an answer (a hit or a clean search
with none) and re-running it would not help.

### 2 — Request evidence: manual is the default

For each row, give the user:

- the sequence, already in the batch file — nothing to look up
- the service and where to submit it (`evidence_acquisition.providers.<name>.service_url`
  in `rules/decision_rules.yaml` — `doctor` does not print these, read the rules file or
  just use the ones below)
- where to hand the result back: `{BATCH}_RETURN_MANIFEST.tsv`, sitting beside the batch,
  every column but one already filled in. The user fills in `path`.

```
HHpred (MPI Bioinformatics Toolkit)   https://toolkit.tuebingen.mpg.de/tools/hhpred
AlphaFold Server                      https://alphafoldserver.com/
Foldseek                              https://search.foldseek.com/search
InterProScan                          https://www.ebi.ac.uk/interpro/search/sequence/
```

Wait for the actual file. **Never accept a description of the result in its place** — a
pasted summary, a screenshot, "the top hit looked like a tail fibre" — none of it is
something the classifier can check, trace or de-correlate. If the user offers one, ask for
the raw download instead.

**AlphaFold is not a direct import.** A folded structure only becomes evidence once
Foldseek is searched against it — there is no manifest kind for a bare structure. Propose
AF3 only when the next real question is fold plausibility (is this an oligomer, does a
proposed geometry hold together), and follow it with a Foldseek search of the result before
expecting anything to come back as evidence.

### 3 — Interpret the raw file, not the top-line score

This is the standard the reference workflow this document is built from actually held to.
An AF3 job can report a low `ipTM` for a trimer and still have assembled a real, symmetric,
tightly-packed interface — that was caught by computing actual inter-chain CA-CA contacts
from the structure file, not by trusting the confidence metric alone. A composition
heuristic (aromatic-residue density suggesting a carbohydrate-binding fold) was proposed,
then *retracted* in the same session when HHpred returned a clean baseplate hit and zero
glycosidase matches — because the direct evidence disagreed with the heuristic, and the
heuristic yielded.

State plainly when something you proposed turns out wrong. That is not a failure of the
process; declining to say so is.

### 4 — Reclassify

The return manifest is already shaped for `--stage2-manifest` — same three required
columns (`cds_id`, `kind`, `path`) the provider reader needs, nothing to reshape:

```
rbpfinder run --genbank <same file> --phage-id <same id> --out <SAME output dir> \
  --stage2-manifest <path>/HHPRED_BATCH_RETURN_MANIFEST.tsv
```

Same `--out`, no `--restart-stage2`. Resuming is the default — only what is still missing
gets asked for again, and nothing already on disk is touched or resubmitted
(`data_preservation`, a standing invariant this project has already paid for once).

### 5 — Converge

Stop when:

- every Primary/Rescue candidate meets the confirmed bar or is excluded with a stated
  reason, or
- a fresh look at the rest of the tail/baseplate module (the existing candidate pool, plus
  composition context already computable without a new submission) turns up nothing further
  worth checking, or
- 2–3 rounds have passed and the user judges the evidence in hand sufficient. That is
  their call, not a rule this document enforces — "if the existing result already proves
  it, I don't think this needs another round" is a legitimate way to stop.

Report the confirmed set first — named, with the evidence behind each — ahead of the rest
of Primary. Rescue and Completeness are reported exactly as `SKILL.md` already specifies;
this loop adds a designation, it does not change what those mean.

## Optional: letting the assistant fetch instead of the user

Manual is the default and should be offered first, every time. Automated fetching is
available on explicit request only, and never assumed from an earlier yes.

**Before using it, say all of this, every time:**

- which service this would submit to, and its URL — the same public service either way;
  this changes who clicks submit, not what leaves the machine or where it goes
- that it costs **materially more tokens** than the manual path — each submission is a
  multi-step browser session (navigate, fill a form, wait for a queue, poll, download),
  not a single API call, and a round with several candidates multiplies that
- that it needs a **yes for this use**, not a standing permission

If given the go-ahead: open the service's `service_url`, submit the batch row's sequence,
retrieve the raw result, save it exactly where a human would have — inside the batch's own
output convention — and fill in the return manifest's `path` column. Then continue at step
4 above exactly as if the user had done it by hand: same parser, same evidence builder, same
convergence rule. Nothing downstream needs to know which way the file arrived.

This is not a new automated provider mode in `rbpfinder` itself — `hhpred`, `alphafold` and
`foldseek` still default to `MANUAL_HANDOFF` in `evidence_acquisition`
(`rules/decision_rules.yaml`), and that default is not being changed here. This is the
assistant, on explicit request, doing by hand by browser what the user would otherwise have
done by hand at a keyboard.

## Dozens of genomes at once

This loop is written for one genome. For bulk-scale work — dozens of phages — do not run
it once per genome; that turns into dozens of separate manual HHpred/Foldseek rounds for
no reason. Use `rbpfinder bulk run` first (`USER_GUIDE.md`), which sweeps every genome
through whatever *is* automatable (local BLAST; InterProScan via its existing remote path,
same consent as a single run) and leaves each genome's own batch files exactly as a single
run would. Then `rbpfinder bulk merge-batches` / `bulk apply-returns` (`ADVANCED.md`)
collapse every genome's *remaining* HHpred/Foldseek need into ONE combined manual round —
the user submits once, and the results route back to each genome automatically (a foreign
row is safely refused, never misattributed — `cds_id` is phage-prefixed and the binding
check already rejects what it does not recognise).

This is not a local-automation workaround for HHpred or Foldseek — both genuinely have no
Windows build on a typical bench machine, the same wall AlphaFold's GPU requirement already
is. `bulk` does not try to route around that; it reduces N manual rounds to one.

Run the per-genome loop above only on what `bulk`'s automated pass could not resolve —
usually a small residual, not all forty genomes.

## What this does not change

Primary, Rescue and Completeness are reported exactly as `SKILL.md` and `OUTPUT_GUIDE.md`
already specify. Nothing here expands Rescue, invents a weighted score, or replaces the
tier system — the confirmed bar sits on top of Primary, and only there.

## If this loop actually produces a confirmed candidate

Say so explicitly, and say what made it so (which two families, at what strength, from
which real submissions). That observation is itself useful beyond the one genome: it is
the evidence `confirmed_rbp_designation`'s shadow gate (`scripts/m22a_confirmed_shadow.py`)
has been waiting to measure before the designation is adopted into `rbpfinder`'s own
output. A developer session extending this project would want to know it happened.
