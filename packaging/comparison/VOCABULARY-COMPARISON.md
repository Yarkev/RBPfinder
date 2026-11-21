# Vocabulary unification — the change, and how to measure it

Nothing in the default path has changed. `vocabulary.mode` is `current`, which reproduces
the frozen behaviour exactly, and 133 tests pass. What follows is the instrument and what
it is for.

---

## 1. What the divergence actually is

Three readers answer one question — *does this text support receptor binding?* — from
three vocabularies. Measured against the tree on 2026-11-18, not estimated:

| | reads | covers | vs `rbp_ontology` |
|---|---|---|---|
| `stage2.annotation_term_map` | homolog and structure hit descriptions | 63 of the ontology's 90 terms | **1 real disagreement** |
| `stage4.direction_map` | a CDS's own product name | 44 of 90 | **3 real disagreements** |
| `rbp_ontology` | (shadow — nothing read it) | 90 | — |

Terms neither live map covers: **0**. The ontology adds no new vocabulary; it is a
reconciliation of two existing ones.

The incremental fixes already did most of the work. `tailspike` was moved into the
positive terms in 2026-09, `lectin` was removed in 2026-09, and what survives is narrow:

**stage2's one disagreement** — `baseplate wedge` (structural_only vs apparatus). Both
readings are non-binding, so it cannot change whether something is an RBP candidate. Not
curated in the ontology, so the live reading may simply be fine.

**stage4's three** — `tail fiber`, `tail fibre`, `tip attachment`. All three are
**curated** in the ontology, which reads them as apparatus membership. `stage4` reads
them as receptor binding.

So the scientific question is one sentence: **does the part name "tail fiber protein"
support receptor binding when it is the CDS's own product name, given that it does not
when it is a homolog's description?**

The 2026-08-19 reasoning for the homolog side was measured: on W073 the Ig-adapter rod,
the real RBP, the connector and the tip *all* have homologs named "tail fiber protein", so
the term identifies the apparatus and cannot discriminate inside it — and leaving it
positive lifted three standing hard negatives past their ceilings. Whether the same holds
for the annotator's own product string is what the benchmark is for.

---

## 2. One defect found before adoption

Adopting the ontology naively would have **regressed N4 gp65**, a standing regression case
confirmed by both adsorption experiments and the 9LC0 cryo-EM structure.

`stage4.explicit_rbp_terms` exists so that an explicit functional claim is not demoted by
a co-occurring structural word. It was implemented in the product-name reader **only**:

```
                                            stage4   stage2   ontology
tail sheath and receptor binding protein    RB  ✓    struct   struct
baseplate and receptor-binding protein      RB  ✓    struct   struct
```

`evidence_families.dual_role_precedence` states the rule in as many words — *"a structural
name may never veto a positive binding result"*, `forbidden: using a structural product
name to exclude a protein from RBP_core` — so the exception should hold on every path, and
a homolog described like gp65 is a positive binding result being vetoed.

The guard is now shared. That is why the comparison has **three** arms and not two: the
guard is a separate, rule-mandated step, and bundling it with the unification would make
any regression unattributable.

---

## 3. The arms

| Arm | What it is |
|---|---|
| `current` | the frozen behaviour. The base for every comparison. |
| `explicit_guard` | `current` + every reader honours `explicit_rbp_terms`. Changes **no** product-name reading; it is a homolog-reader fix. |
| `unified` | `explicit_guard` + the ontology decides direction for product names too. |

`unified` does **not** adopt the ontology's `interpretation_confidence`, which would move
*strengths* as well as directions. That is a second variable and belongs in its own
measurement.

```bash
rbpfinder --genbank <file> --phage-id <id> --out <dir> --vocabulary unified
```

The mode is recorded in `run_result.json` under `vocabulary`, so a number is always
attributable. A result is only comparable with another result from the same mode.

---

## 4. Measured effect on a probe genome

A 12-CDS genome with PHROG annotations, so the product-name direction reaches the D
family (without an annotation it only feeds a weak G record, which loses family
representation to C1 and hides the change — worth knowing before reading any result):

```
CDS   product                                    current        unified
0004  tail fiber protein                         R2-E2  rank 1  R3-E2  rank 7   ***
0006  tip attachment protein                     R2-E2          R3-E2          ***
0005  tailspike protein            (control)     R2-E2  rank 2  R2-E2  rank 1
0007  tail sheath and receptor binding (gp65)    R2-E2  rank 4  R2-E2  rank 2
      everything else                            unchanged      unchanged

Primary:  [0004, 0005, 0006, 0007]  ->  [0005, 0007]
```

`explicit_guard` produced **zero** differences from `current` on this genome — no tier,
Primary, pool or rank change. That is the attribution point: the guard is a homolog-reader
fix, so everything in the table above is attributable to the unification alone.

Both directions are visible. The two part-name candidates leave Primary; the two genuine
ones **move up** the ranking because the part-name false positives are no longer above
them. Which of those matters more is an empirical question, not a stylistic one.

Harness output for this run, which the table is read from:

```
false-complete     0        hard neg lifted    0
tier changes       2        pool changes       0
Primary changes    1        reference moves    2
```

---

## 5. The risk this must resolve

**Measured, 2026-11-19, on T7: `unified` gives the wrong answer and the default gives the
right one.**

This section used to state the risk as a prediction. A Biomni test round resolved it on
the literature gold standard:

```
mode             T7 accurate route (phold.gbk + S + T)      gp17
current          Primary: CDS_0039, CDS_0040, CDS_0052       R2-E3   in Primary
explicit_guard   Primary: CDS_0039, CDS_0040, CDS_0052       R2-E3   in Primary
unified          Primary: CDS_0039, CDS_0040                 R3-E3   OUT
```

gp17's product name is "tail fiber protein", so under `unified` its **D** record flips
from receptor binding to apparatus. Nothing else supplies a receptor-binding direction:
its S homologs are part names (apparatus 10/10 against a full RefSeq viral database) and
its PDB titles are part names or crystallographic phrases. All four families then read
apparatus and it caps at `R3`.

Note what this means, because it is not the result the unification was expected to have:
**the divergence `unified` removes is what produces the correct answer here.** The same
words read as receptor-binding through D and apparatus through S, and that disagreement
is what lifts gp17. Reconciling the two maps is therefore not obviously a cleanup, and
the burden of proof has moved onto `unified`.

One strain is one strain. But it is the gold-standard strain, and it is also the strain
this whole line of work started from.

So the decisive question for the benchmark is not "did anything change" but:

> **Do the reference RBPs keep their tier and their place in Primary?**

If they do, `unified` is a precision gain with no recall cost. If gp17-class proteins drop
out, the part-name reading is load-bearing for recall on poorly-annotated genomes and
`unified` should not be adopted as it stands — the ontology would need the functional
vocabulary to reach those proteins another way first.

---

## 6. Running the comparison

```bash
python3 packaging/comparison/compare_arms.py \
    --manifest genomes.tsv --out-root runs/vocab \
    --arm current:--vocabulary=current \
    --arm explicit_guard:--vocabulary=explicit_guard \
    --arm unified:--vocabulary=unified \
    --labels sma_runs/scores.json \
    --hard-negatives SMA_hard_negatives.tsv \
    --shared=--foldseek-db=pdb100=/workspace/fsdb/pdb \
    --shared=--prostt5-model=/workspace/fsdb/weights \
    --shared=--host="Stenotrophomonas maltophilia"
```

Write `--shared` and `--arm` values with `=`; a value starting with a dash is otherwise
read as another option name. The run resumes, so an interrupted sweep continues where it
stopped.

**Run the accurate route (phold), not just a configured database.** Running this under
evidence starvation measures the change against a crippled baseline. More than that: the
T7 result above only appears on the phold route, because the D family that `unified`
flips exists only when there is a PHROG annotation to produce it. A fast-route sweep
would show `unified` changing nothing on gp17 and would answer the wrong question.

### Acceptance

| Must hold | |
|---|---|
| `false-complete` = 0 | in **every** arm. The invariant that cannot be traded. |
| hard negatives at their ceilings | a lifted negative is not an improvement |
| reference RBPs keep tier and Primary | §5 — the decisive one |
| complete-recall@K no worse | the headline metric |

| Worth reporting either way | |
|---|---|
| Primary membership changes | what a user would act on |
| reference RBP rank moves | precision gain shows up here as upward moves |
| pool changes | a CDS gained or lost as a candidate altogether |
| `explicit_guard` differing from `current` at all | it should not, on product names; if it does, look there before reading `unified` |
| candidates whose role label changed | the helper-noun cost in §7 |

Tier changes are reported over **every** candidate, not just the labelled ones — a
vocabulary change shows up first as an unlabelled false positive losing its tier. Rank
moves stay label-scoped, because a rank move only means something when the CDS is known.

The harness prints these and writes `comparison.json` with the per-genome detail. It makes
no judgement about whether a change is good; that is the reader's call.

---

## 7. Known costs of `unified`, recorded rather than hidden

**The helper-noun distinction is lost.** `rbp_ontology` has no helper terms, so "tail fiber
assembly protein" reads `apparatus` instead of `structural_only`. Both are non-binding, so
a chaperone is still not promoted, but the role label moves from
`structural_tail_component` to `unresolved`. Adding helper terms to the ontology is a
further vocabulary change with its own measurement.

**`baseplate wedge` moves** from structural_only to apparatus. Non-binding either way.

**28 terms gain an opinion** the product-name reader did not have — LamG, CBM, discoidin,
hyaluronidase, xylanase and others toward receptor binding; collagen, fibritin, Ig-like,
trimerisation toward structural_only. The first group is a recall gain. The second is new
competition for family representation, which is not neutral: a `structural_only` record
can win a family's representative slot where `uninformative` could not.

---

## 8. If `unified` passes

Promote it by changing `vocabulary.mode` in the rules file, in a commit that cites the
comparison output. Keep `current` as a selectable mode: it is how a historical result
stays reproducible.

If it fails, the useful outcome is not "keep current" but **which** control broke, because
that says whether the fix is a better ontology or a different place to apply it.
