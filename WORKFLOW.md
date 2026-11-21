# The complete workflow, for review

Read from the code, not from SKILL.md. Where the two disagree, §5 says so.

Current as of **`v1.2.4+linux`**. Revised 2026-11-21 against a Biomni test round, which
corrected an attribution in §2 that was mine and wrong — the correction is quoted in
place rather than edited away.

Two halves that are deliberately separate: **what the agent does** (§1) and **what one
run does inside the tool** (§2–§4). The split is the design — the agent is an operator,
and every scientific decision is in the frozen executor. §5 is the list of places a wrong
answer can come from, which is what is worth your attention.

---

## 1. The agent's workflow

```
 0  scope check          Caudoviricetes only -- say so BEFORE running, not after
 1  ask fast or accurate default is fast = no phold
 2  annotate             pharokka (+ phold only if asked)      <- the AGENT's job
 3  validate-input       seconds, changes nothing, fails loudly
 4  doctor               which evidence families can even run
 5  bootstrap            configure what is missing, then re-check
 6  run                  rbpfinder --genbank ... --out ...
 7  report               Primary / Rescue / Completeness + versions
```

Steps 3 and 4 before step 6 is the part that is easy to skip and expensive to skip.
`validate-input` uses the same readers the analysis uses, so it catches a bad input in
seconds rather than after an hour of phold. `doctor` decides whether a thin result will
mean *evidence starvation* or *a genuinely unresolved genome* — and that distinction
cannot be recovered after the fact from the output alone.

**Annotation is outside the tool on purpose.** If RBPfinder annotated, a pharokka or
PHROGs upgrade could silently move a tier, and no one would see it in a diff.

**Default is no phold.** What phold buys is annotation *quality* — it resolves CDS that
pharokka leaves as "hypothetical", which improves nomination and the direction read off a
product name. It does **not** add a second structural family: de-correlation collapses a
phold hit and a Foldseek hit into one T by design. Hand over phold's **GenBank**, never
its per-CDS TSV, which carries no protein sequence and would leave structure search
silently unavailable.

---

## 2. What one run does

```
Stage 0  ingest          GenBank -> CDS rows, topology, architecture typing
Stage 1  nominate        C1 C2 C3 C4 C5  ->  the candidate POOL        (add-only)
Stage 2  sequence        local BLAST or imported results      -> family S
Stage 3  structure       local Foldseek + ProstT5             -> family T
Stage 4  normalise       per CDS: evidence records with direction + strength
         de-correlate    records -> independent FAMILIES
Stage 5  tier            tier_r x tier_e per CDS
Stage 6  audit loop      classify -> audit -> reopen -> reclassify   (capped)
Stage 7  rank + sets     ranking -> Primary / Rescue
```

### Stage 0 — ingest

CDS rows with `/translation` and some `/locus_tag`. A tag that does not end in
`CDS_<number>` is accepted: CDS are renumbered **in genome coordinate order** (not file
order), the original is kept in `raw_locus_tag`, and a warning is emitted.

Topology is resolved here — declared, or detected — and `is_circular` propagates to C1,
C4, C5 and audit A3, so adjacency wraps the origin instead of splitting at it.

Architecture typing (sipho/myo/podo) is a **soft prior**. It reaches `k_default` and the
priority loci, and nothing else. There is no code path from architecture to nomination, so
a wrong type cannot exclude a candidate.

### Stage 1 — nomination, add-only

| | channel | what nominates |
|---|---|---|
| C1 | module position | inside the tail-module window, anchored on Tal/Dit |
| C2 | keywords | whole-token match in the product name |
| C3 | dark matter | unannotated CDS inside the tail-module window |
| C4 | tandem cluster | member of a nominated tandem cluster |
| C5 | comparative | variable against a conserved backbone (needs a cohort) |
| C6 | external ML | **not implemented** — see §5 |

`invariants.nomination_is_add_only` is an actual assertion, per channel, not a comment: a
channel that shrank the pool aborts the run. The only exception is `--suppress-nomination`,
a test hook.

The pool is **recall protection sized for the algorithm, never the experimental
shortlist.** Reporting it as a shortlist is the most consequential misreading available.

### Stage 2 / Stage 3 — the two evidence searches

Stage 2 is family S, from a local BLAST database or imported results. **With no database
configured, S does not exist.** S carries *functional* homolog annotations when the
database has them — and that qualifier is load-bearing, which an earlier version of this
document got wrong.

> **Corrected 2026-11-21, against a Biomni test round.** This said the T7 gp17 case moved
> `R3` → `R2` and into Primary "on nothing but a configured phage database". It does not.
> With a full RefSeq viral protein database (722,823 sequences) gp17's S top-10 hits are
> *all* annotated "tail fiber protein" — apparatus consensus 10 of 10 — so its S record
> reads `supports_apparatus_membership`. gp17 goes `R3-E1` → `R3-E3` and **never enters
> Primary**. More families raise `tier_e`; they cannot raise `tier_r`.
>
> What actually produces the promotion is a **D family carrying a receptor-binding
> reading**, which needs a PHROG/phold annotation: phold's `phrog:4242` gives gp17 a D
> record, the D term map reads "tail fiber protein" as `supports_receptor_binding`, and
> that one direct family takes it to `R2` and into Primary.
>
> The error was mine and the evidence was in front of me: the family list I was given
> was `D+G+S+T`, D included. I saw S appear and credited the tier change to it without
> checking which family R2 actually requires.

So the operative advice is not "configure a database" but **configure a source of
functional annotation**. A database of part names raises how well you looked, not what
you concluded. See §5.1 and §5.2.

Stage 3 is family T, from local Foldseek with ProstT5 (sequence → 3Di, no structure
prediction step, nothing transmitted). Default scope is the **nominated pool**;
`--foldseek-all-cds` widens it to every CDS, which is what makes audit A2 runnable.

On the ProstT5 route `probability_pct` is always null — a sequence has no backbone
coordinates, so foldseek cannot compute one. Expected, not a failure.

### Stage 4 — direction, then de-correlation

Each CDS's records get a **direction** and a **strength**:

| direction | means |
|---|---|
| `supports_receptor_binding` | a *functional* term: tailspike, adhesin, depolymerase, "receptor binding" |
| `supports_apparatus_membership` | a *part* name: "tail fiber protein", "distal tail", baseplate wedge |
| `supports_structural_only` | tail tube, sheath, connector, adaptor, "tail protein" |
| `supports_non_rbp_identity` | portal, terminase, polymerase — a positive non-RBP call |
| `uninformative` | nothing matched (**most PDB titles**, and that is not "no evidence") |

Two rules that matter more than they look:

* **A compound name takes the conservative reading.** "tail fiber assembly protein" is a
  chaperone. "endonuclease … uracil glycosylase Ung" is DNA repair, and once reached
  Primary because `glycosyl` matched inside `glycosylase`.
* **An explicit functional claim is never vetoed by a co-occurring structural word.**
  "tail sheath and receptor binding protein" (N4 gp65) reads as receptor binding.

Then de-correlation: records collapse into **independent families**, and the rule is that
it **counts families, never tools**. Two databases are one S. A PHROG keyword, a phold top
hit and a structure hit are frequently the same fact seen three times.

### Stage 5 — two axes

```
tier_r   R1  >=2 RB families INCLUDING one direct (T/D/S), no hard contradiction
             -- G-only support is explicitly forbidden from R1
         R2  strong G or C plus one more family, direct evidence incomplete
             -- or strong T with ambiguous/absent context
         R3  clearly part of the apparatus, no direct recognition evidence
         N   positive non-RBP assignment (hard exclusion)

tier_e   E3  >=3 independent families      E2  2      E1  1, or unresolved
```

`tier_r` is *what it is*; `tier_e` is *how well we looked*. Keeping them apart is why a
poorly-evidenced run cannot be mistaken for a confident negative.

### Stage 6 — the closed loop

```
classify -> audit -> reopen -> reclassify -> audit -> ... until no FRESH hits, or cap
```

| audit | asks |
|---|---|
| A1a | which tail/morphogenesis-module CDS never entered the pool? |
| A1b | unknown CDS adjacent to a real tail component — **deliberately blind to the C1 window**, so it still works when C1 built every window in the wrong place |
| A2 | genome-wide, which CDS have positive structural signal but negative positional signal? |
| A3 | for every assigned RBP, do the ±N flanking CDS hide an accessory RBP or distal tip? |
| A4 | is every comparative variable locus explained by an assigned candidate? |

A reopened CDS gets a provenance channel (`S6-A1`, `S6-A3`, …) and is reclassified like
any other. `stage6_recovery_confers_no_tier_advantage`: being recovered contributes
nothing to `tier_r` or `tier_e`, so a Stage 1 recall failure stays visible instead of
being laundered into a confident candidate.

**A2 is the one with a platform condition.** It needs genome-wide structural signal, so on
the default pool-scoped path it reports itself *unexecutable* — which is the design
behaving correctly, not a fault. `--foldseek-all-cds` makes it runnable, and is **not a
no-op on the result**: A2 reopens the pool, reopened CDS are classified normally, and
Stage 7's Primary top-up can admit them (measured: Primary 1 → 3 on one strain).

`S6-A2` is deliberately **absent** from the context channels that mint a G record. A2's
premise is that positional signal is negative; minting an apparatus-membership record
would assert the opposite and hand the CDS a free family. Tests pin the omission.

### Stage 7 — ranking and the two sets

Ranking: `tier_r` → `tier_e` → family count → RB family count → max RB strength →
`cds_id`. The last key makes it deterministic.

```
Primary   R1, topped up with the strongest R2     -> test these first
Rescue    remaining R2, plus R3 with a direct hint -> carried to protect recall
```

**Truncation to k is forbidden.** Six R1/R2 candidates are reported as six;
`k_is_not_a_stopping_rule`. And **Rescue is never expanded to make recall look better** —
when the evidence is insufficient the correct output is `completeness: limited` with the
reason, because padding destroys the only signal the user has about how much to trust the
run.

---

## 3. What a run leaves behind

`run_result.json` carries, among its top-level keys:

```
versions              {package, rules, schema}   <- new; quote it when you report a number
vocabulary            {mode, overridden}
presentation_signals  hints for a human reader -- NEVER evidence; see §4a
top_unresolved_candidates   CONDITIONAL -- see below
primary_set
rescue_set
candidates            every pool member, with its channels, families, tiers, role
completeness_assessment   complete | partial | limited
limitations           which audits reported themselves unexecutable, and why
stage2_local / stage3_local   did S and T run, and over what scope
capability_matrix     one canonical answer about what was available
run_flags             e.g. REMOTE_SUBMISSION_BLOCKED, STRUCTURAL_SCREEN_UNAVAILABLE
```

`top_unresolved_candidates` is present only when the shortlist is empty or
`completeness` is `limited`, so the key count is **32 or 33** at `v1.2.4+linux` — not a
fixed number, and not a number worth quoting: this document has stated it wrong three
times (29, then 31, then 31-or-32, each true of a different commit). Count it from a run
if you need it. It is presentation-only: those CDS are not promoted to Rescue and are not counted
in any recall figure. Its absence carries no information `primary_set`, `rescue_set` and
`completeness_assessment` do not already give. Making it always present with `null` would
be the tidier contract and matches this project's preference for explicit states over
absence; it is deliberately **not** done yet, because a consumer written as
`if "top_unresolved_candidates" in d: ...` would start reading a `None`, and test rounds
are in flight.

Exit codes: `0` success, `2` validation failed **or an internal schema violation**,
`3` missing capability, `1` unexpected internal defect. Exit 2's two meanings are why the
message has to be read before blame is assigned — the schema case means the tool produced
a record its own contract forbids, and the user's input is fine.

---

## 4a. Presentation signals — hints, not evidence

Added at `v1.2.3+linux`. Three checks run **after** ranking and after the candidate sets
are fixed, so there is nothing downstream for them to influence:

| signal | fires when |
|---|---|
| `structure_annotation_conflict` | the product name claims a fibre while a hit *description* describes baseplate or assembly machinery |
| `fibre_chaperone_adjacency` | a fibre assembly chaperone sits within 2 CDS downstream **on the same strand** |
| `direction_conflict` | the candidate's own records disagree — one says receptor binding, another says structural-only or non-RBP |

They exist because of a false positive no evidence family could have caught: a protein
annotated "tail fiber protein" whose 99.3%-identity homolog was *also* called "tail fiber
protein", which a cryo-EM map showed to be a tube initiator. **Agreement between two
readers of the same part name is not two independent lines of evidence about function** —
but de-correlation collapses tools, not vocabularies, so both records stood and nothing
in the output said why to doubt them.

Four constraints, each enforced rather than promised:

1. **Inert.** `tests/test_presentation_signals.py` fills the whole vocabulary with terms
   matching everything — measured: 16 of 16 candidates firing, 196 hits on the letter `a`
   alone — and asserts the pool, families, tiers, ranking and Primary/Rescue are
   byte-identical. If that test can be made to fail, the block has escaped its layer.
2. **`calibration: pending`**, the same status as the tiers: one measured case plus the
   literature, no fitted trigger rate.
3. **Trigger rate observable.** `term_trigger_counts` reports per-term hits, because a
   hint firing on every candidate is noise and one that never fires is dead weight.
   `complex` is the term to watch — PDB titles say it constantly.
4. **No automatic downgrade.** A hint invites a human look. It never moves a tier.

## 4. Offline, unconditionally

`--allow-remote`, `--data-policy remote_allowed` and `--stage2-backend ebi` are each
refused with **exit 2 before any ingest**. This is not a property of a build; it is
unconditional in the source, so there is no other build to go to. `--help` still lists
`ebi` as a parseable choice, deliberately, so an old command line gets the explicit
refusal and its reason instead of argparse's "invalid choice".

Evidence therefore comes from a local BLAST database, local Foldseek, imported results, or
nothing — in which case the run reports honestly what it could not do.

---

## 5. Where a wrong answer can come from

This is the section to argue with. The evidence for several of these — and the round that
falsified §2's earlier claim — is archived in `docs/test-rounds/`, with the regression
baselines to check against.

### Live, and known

1. **Evidence starvation reads like a biological finding.** The diagnosed cause of the
   original complaint. With no DB_PHAGE there is no S family, so a real RBP sits at `R3`
   with `G+T` and never enters Primary. The output does say so — `stage2_local`,
   `limitations`, `capability_matrix` all carry it — but only if someone reads them. The
   skill makes checking those mandatory before any statement about the biology.

2. **Part-name conservatism.** `tail fiber protein` is the most common real-world RBP
   description, and in the default vocabulary it reads as receptor-binding on a CDS's own
   product name but as apparatus on a homolog's description. The two live term maps
   disagree on 3 terms; `rbp_ontology` reconciles them and `--vocabulary unified` applies
   it, **but the default has not moved** and will not until the comparison says so.
   **No longer only a risk — measured, on the literature gold standard.** On T7 with a
   phold annotation, `unified` pushes gp17 out of Primary (`R2-E3` → `R3-E3`) while
   `current` and `explicit_guard` keep it in. So on T7 `unified` gives the biologically
   wrong answer and the default gives the right one.

   The uncomfortable part: the divergence `unified` removes is **what produces the correct
   answer here.** The same words, "tail fiber protein", read as receptor-binding through
   D (PHROG keyword) and as apparatus through S (homolog description), and that
   disagreement is exactly what lifts gp17. Reconciling the two maps is therefore not
   obviously a cleanup. One strain is one strain, but it is the gold-standard strain, so
   the burden of proof has moved onto `unified` rather than sitting on the default.
   See `packaging/comparison/VOCABULARY-COMPARISON.md`.

3. **`decorrelate` picks a family's representative by strength first.** So a `weak`
   receptor-binding record loses the slot to a `moderate` apparatus one — observed on T7
   CDS_0052. Strength-first is the documented primary key, so changing it is a scientific
   decision needing a benchmark, not a fix.

4. **One record carries two different claims.** A family's representative asserts both
   *this family counts* and *what this family supports*, so an intra-family conflict
   cannot be expressed — even though `stage3.af3_priority` treats exactly such a conflict
   as a P1 trigger. Structural, and the deepest item on this list.

5. **`uninformative` dominates when searching PDB.** Titles are crystallographic
   descriptions, not functional annotations. A structural hit can be excellent and carry
   no functional word, so T rarely lifts a true RBP alone.

15. **The presentation signals' vocabulary is unmeasured.** §4a. Nine assembly-context
    words and four part names, from one measured case. On the synthetic probe genome the
    default vocabulary fires **zero** times, which says it is not trivially noisy and
    nothing more — the real rate needs the labelled strains. `complex` is the term most
    likely to be pruned. Being inert, a wrong vocabulary here costs attention, not
    correctness.

### Not implemented, and advertised as such

6. **C6 and the M family.** The rules specify an external-ML channel and an M family;
   no channel implements them. `ml_results` is accepted by A2 and the capability probe,
   both with tested branches, and is never passed — wiring C6 is supplying it at
   `cli.py:588` and nothing else. The hooks named (PhageRBPdetect, DepoScope, SpikeHunter)
   are all `calibration: pending`, and PLM methods have moved on since.

7. **`--foldseek-all-cds` is experimental.** Pilot: 6 labelled strains, 12 runs,
   `false-complete` 0 in all 12, complete-recall@K identical in both arms, 362
   reopened-and-downgraded against 2 reopened-and-promoted. The 48-strain regression has
   not been run. Say which path produced a result, because the completeness state depends
   on it.

8. **Every threshold is `calibration: pending`** in the sense that matters: `meta`
   records `provenance: reverse-engineered from SMA phage work; not yet calibrated`. The
   tiers are expert-derived starting points, not fitted values.

### Where the documentation and the code can drift apart

9. **This has already happened once, in seven places at once.** Remote submission was
   removed in the code, and the desktop skill, four of its references, the
   `--stage2-backend` help, the refusal messages' own parenthetical, two READMEs, the
   acquisition plan and both installers/verifiers all still advertised it — two of them as
   *passing checks that required it to be advertised*. Tests now assert the agreement.
   The general risk is unchanged: the code is one place and the things describing it are
   several.

10. **Two skill packages can diverge.** Desktop and Linux are not interchangeable —
    family T is only reachable inside a run on Linux. Each now states which it is and
    points at the other, and the bundle ships both with each installer picking its own.

### Carried, documented, not fixed

11. **Window merging happens in rotated linear space.** If two windows *both* cross the
    cut they can overlap on the circle without merging. Needs the largest anchor-free gap
    to be smaller than two extensions reaching into it — rare, unlike the origin-splitting
    defect it replaced.

12. **`unified` has no helper-noun vocabulary**, so a chaperone would read `apparatus`
    rather than `structural_only` under it. Both non-binding, so nothing is promoted, but
    the role label degrades.

13. **Dead remote code is still in the tree** (`stage2_remote`, `interpro_remote`,
    `_remote_consent`; `interpro_remote` has no caller at all). Unreachable, not dangerous
    — but finding it is not evidence a remote route exists.

14. **Bare `receptor` and bare `lectin` are absent from the stage2 term map**, so
    "receptor recognition protein" and "C-type lectin domain-containing protein" read as
    uninformative on a homolog description.

---

## 6. The invariants, in one place

These are assertions, not guidelines. If one can be violated, that is a defect:

```
nomination_is_add_only                    a Stage 1 channel may only add
absence_is_not_counter_evidence           a search that found nothing lowers no tier
searched-no-hit is not not-run            recorded as different states
architecture_is_soft_prior                reaches k and priority loci, nothing else
k_is_not_a_stopping_rule                  no truncation to k
every_cds_has_a_reason                    every pool member carries why it is there
stage6_recovery_confers_no_tier_advantage a recovered CDS gains no tier from being recovered
dual_role_precedence                      a structural name never vetoes a positive binding result
one_homology_family_stays_one_S           however many databases were searched
```

And the operating rule that sits above all of them: **never edit `decision_rules.yaml` to
make a run succeed or a result look better.** Repairing an executor contract that is
demonstrably wrong against an upstream tool is legitimate, and must be recorded in
`CHANGES-linux.md` with the evidence. Those are different acts.
