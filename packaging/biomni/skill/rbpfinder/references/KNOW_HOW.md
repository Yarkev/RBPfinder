# Identifying receptor-binding proteins in a tailed phage genome

A standardised route from one phage genome to a defensible RBP shortlist, for
*Caudoviricetes* (tailed dsDNA phages). The receptor-binding protein is usually a
tailspike (often carrying depolymerase activity) or a tail fibre.

## The division of labour, and why it matters

    annotation   pharokka (and phold on the accurate route)   <- the orchestrator's job
    judgment     RBPfinder                                     <- the only thing that
                                                                  decides what is an RBP

Do not do the judgment step by reading the evidence and ranking candidates yourself. That
is where naive pipelines go wrong, in three specific ways RBPfinder exists to prevent:

- **Correlated evidence counted as independent votes.** A PHROG keyword, a phold top hit
  and a structure-database hit are frequently the *same* fact seen three times. Reading
  "three evidence chains agree" as three votes manufactures confidence that is not there.
  RBPfinder de-correlates into evidence families and counts families, never tools.
- **Apparatus mistaken for binder.** A protein named "tail fibre assembly protein" is a
  chaperone. A baseplate or an Ig-like adapter rod sits in the tail without binding
  anything. Proximity and tail-ish naming are not receptor binding.
- **Always producing a confident answer.** Sometimes the evidence genuinely cannot
  narrow the candidates. RBPfinder then reports `completeness: limited` and says why.
  That state is the most useful thing in the output; a ranked list invented in its place
  is worse than no list.

## Procedure

1. **Settle the route with the user.** Call `rbpfinder_workflow_plan()` with no mode; it
   returns a question. Ask it. The two routes:
   - **fast** (default): pharokka, then RBPfinder with local Foldseek structure search
     over the candidate proteins. Minutes. Structural evidence is still used.
   - **accurate**: additionally runs phold genome-wide first, which resolves proteins
     pharokka leaves as "hypothetical" and so improves nomination. 1–2 h extra on CPU.
   If the user has no preference, use fast. Prefer accurate when the genome is poorly
   annotated, or when a fast run came back `limited`.

2. **Annotate.** Run the commands `rbpfinder_workflow_plan(mode)` returns. Verify the
   flags against the installed version (`pharokka.py --help`, `phold run --help`) — they
   move between releases. What is fixed is the deliverable: an annotated GenBank whose
   CDS carry `/translation` and whose locus tags end in `CDS_<number>`.

   Hand over **phold's GenBank** (`phold.gbk`), not its per-CDS predictions TSV. The table
   carries no protein sequence, so local structure search would be silently unavailable.

3. **Validate before analysing.** `check_annotation(genbank_path=...)` takes seconds and
   fails with a precise reason. On the accurate route, validate the pharokka GenBank too,
   *before* committing an hour to phold. If it is not usable, report the problem — do not
   proceed.

4. **Analyse.** `run_rbpfinder(genbank_path, phage_id, out_dir, mode, host=...)`. Give
   `host` as "Genus species" when known: a matching host database materially improves the
   result, and a non-matching one is skipped rather than misapplied.

5. **Report, in this order and with these names.**
   - **Primary** — the candidates to test first.
   - **Rescue** — kept only to protect recall; the evidence is *not* strong. Never expand
     it to make recall look better.
   - **Completeness** — `complete` / `partial` / `limited`, with the stated reasons. Never
     present a `partial` or `limited` run as biologically complete.
   - `all_CDS_screening.tsv` answers "why was this CDS not a candidate?" — cite it when
     the user asks about a protein that did not make the list.

   Quote the tiers as produced (e.g. `R1-E3`). Do not re-rank, and do not add candidates.

## Invariants to preserve when summarising

- **searched-no-hit is not not-run.** "We looked and found nothing" and "we never looked"
  are different states, and the output distinguishes them.
- **Absence of evidence is not negative evidence.** A search that found nothing never
  lowers a candidate's tier.
- **One sequence-homology family stays one family**, however many databases were searched.
  Two databases are not two independent lines of evidence.
- An explicit functional claim ("receptor binding protein", "adsorption protein") is not
  overridden by a structural word elsewhere in the same product name; but a compound name
  whose head noun is a helper ("…assembly protein", "…chaperone") takes the conservative
  reading.

## Scope and limits

- Tailed dsDNA phages only. Do not claim validated support for RNA, ssDNA, filamentous or
  tailless phages, or archaeal viruses.
- A bare genome FASTA cannot be analysed directly — annotate it first (step 2). RBPfinder
  refuses it rather than guessing a gene caller, because a different caller changes CDS
  boundaries.
- Local structure search needs Foldseek, a structure database and the ProstT5 weights on
  the machine; `rbpfinder doctor` reports whether `Local Stage 3` is AVAILABLE. It is
  Linux-only (Foldseek has no native Windows build) and transmits nothing.
- Remote EBI BLAST transmits candidate protein sequences off the machine. It is off by
  default; only enable it after telling the user, and never for unpublished sequences.
- The output is a prioritised list of experimental candidates. It is not validated
  biology, and it does not promise to find every real RBP in every phage.

---
*Attribution: derived from the RBPfinder tool's own Agent Skill and decision rules
(`packaging/skill/rbpfinder`, `rules/decision_rules.yaml`), which hold the authoritative
version of these invariants.*
