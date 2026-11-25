# Identifying receptor-binding proteins in a tailed phage genome

A standardised route from one phage genome to a defensible RBP shortlist, for
*Caudoviricetes* (tailed dsDNA phages). The receptor-binding protein is usually a
tailspike (often carrying depolymerase activity) or a tail fibre.

This is the reasoning companion to `SKILL.md`, which holds the commands and flags. Where
the two differ, `SKILL.md` wins.

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
- **Apparatus mistaken for binder** — within limits. "Tail fibre assembly protein" is a
  chaperone, and an Ig-like adapter rod sits in the tail without binding anything. But a
  tailspike is one protein that is both, and the tool maps it to receptor binding
  deliberately. Proximity and tail-ish naming are not receptor binding; a functional term
  is.
- **Always producing a confident answer.** Sometimes the evidence genuinely cannot narrow
  the candidates. RBPfinder then reports `completeness: limited` and says why. That state
  is the most useful thing in the output; a ranked list invented in its place is worse
  than no list.

## Procedure

Everything is done by running the `rbpfinder` executable. There is no Python API to call.

1. **Check what this installation can do.** `rbpfinder doctor`. Which capabilities are
   AVAILABLE changes what the run can conclude, so read it before promising anything.

2. **Settle the route with the user.** fast (pharokka only, minutes) or accurate
   (pharokka then phold genome-wide, 1–2 h extra CPU). Default fast. Prefer accurate on a
   poorly annotated genome, or after a fast run came back `limited`. What phold buys is
   annotation quality, not a second structural evidence family.

3. **Annotate.** Verify the flags against the installed version (`pharokka.py --help`,
   `phold run --help`) — they move between releases. What is fixed is the deliverable: an
   annotated GenBank whose CDS carry `/translation`. Locus tags in any format are
   accepted; tags that do not end in `CDS_<number>` are numbered by genome coordinate
   order, with the original preserved and a warning emitted.

   Hand over **phold's GenBank** (`phold.gbk`), not its per-CDS predictions TSV. The table
   carries no protein sequence, so local structure search would be silently unavailable.

4. **Validate before analysing.** `rbpfinder validate-input --genbank <file>` takes
   seconds and fails with a precise reason. On the accurate route, validate the pharokka
   GenBank too, *before* committing an hour to phold. If it is not usable, report the
   stated problem — do not proceed.

   Heed the topology warning. An undeclared circular genome loses its C1/C3 nominations
   across the origin while the pool size stays the same, so nothing in the output reveals
   the degradation.

5. **Analyse.** `rbpfinder --genbank <file> --phage-id <id> --out <new-dir>`, plus
   `--host "Genus species"` when a matching host database is configured, and the local
   Foldseek flags when a structure database is configured.

6. **Report, in this order and with these names.**
   - **Primary** — the candidates to test first.
   - **Rescue** — kept only to protect recall; the evidence is *not* strong. Never expand
     it to make recall look better.
   - **Completeness** — `complete` / `partial` / `limited`, with the stated reasons. Never
     present a `partial` or `limited` run as biologically complete.
   - `all_CDS_screening.tsv` answers "why was this CDS not a candidate?" — cite it when
     the user asks about a protein that did not make the list.

   Quote the tiers as produced (e.g. `R1-E3`). Do not re-rank, and do not add candidates.

## What the evidence can and cannot do

- **No single family settles it, and which one matters depends on the CDS.** Measured on
  T7: gp17, the literature-established RBP, came out `R3 / apparatus` with a strong
  structural hit, because every PDB title for it is a part name or a crystallographic
  phrase.

  **Adding a sequence database did not fix that**, which an earlier version of this file
  got wrong. With a full RefSeq viral protein database (722,823 sequences) gp17's top-10
  homologs are *all* annotated "tail fiber protein" — apparatus, 10 of 10 — so it went
  `R3-E1` → `R3-E3` and never entered Primary. **More families raise `tier_e`; only a
  direction raises `tier_r`.** What moved it was a **D** family from a PHROG/phold
  annotation, where the same words read as receptor binding.

  So the operative advice is *configure a source of functional annotation*, not
  *configure a database*. And family T is not merely corroboration: on a **dark-matter**
  CDS — "hypothetical protein", so no D, and no homolog hit, so no S — it is the only
  family that can say anything at all. `G` alone caps at `R3-E1` and can never enter
  Primary; `G` plus a functional T hit reaches `R2-E2`.
- **The same product name is read differently by different families, on purpose.**
  "tail fiber protein" reads as receptor binding through the D vocabulary and as apparatus
  membership through the S consensus vocabulary (moved there 2026-08-19, measured: on
  W073 the Ig-adapter rod, the real RBP, the connector and the tip all carried homologs
  with that name, so it identifies the apparatus and cannot discriminate inside it). That
  divergence is deliberate, and on T7 it is what lifts gp17. Do not "fix" it by reading
  one table and assuming the other agrees.
- **`uninformative` dominating a PDB search is expected.** PDB titles describe crystals,
  not functions. It does not mean "no evidence".
- **An empty Primary is not a biological finding** until you have checked whether S and D
  could run at all. Distinguish evidence starvation from a genuinely unresolved genome,
  and report the families that did not run alongside the result.
- **When the literature disagrees with the output, say so explicitly**, give the mechanism
  (usually part-name conservatism plus evidence starvation), and state that the tool's
  conclusion stands. Pointing out a disagreement is not re-ranking.

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
- A bare genome FASTA cannot be analysed directly — annotate it first. RBPfinder refuses
  it rather than guessing a gene caller, because a different caller changes CDS boundaries.
- **There is no remote path at all**, and not only in this build: the refusal is
  unconditional in the source, so there is no other build to send a user to. EBI BLAST,
  remote InterProScan, HHpred web, Foldseek web, the AlphaFold Server and cloud
  annotation are refused with exit code 2. Never suggest them and never transmit a user
  sequence anywhere.
- Local structure search needs Foldseek, a structure database and the ProstT5 weights on
  the machine; `rbpfinder doctor` reports whether `Local Stage 3` is AVAILABLE. It is
  Linux-only (Foldseek has no native Windows build) and transmits nothing.
- The output is a prioritised list of experimental candidates. It is not validated
  biology, and it does not promise to find every real RBP in every phage.

---
*Derived from the RBPfinder tool's own decision rules (`rules/decision_rules.yaml`), which
hold the authoritative version of these invariants.*
