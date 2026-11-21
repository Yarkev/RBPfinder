# Test rounds — the evidence behind the changelog

Reports from test rounds run outside this repository, archived **verbatim**. They are
evidence records: their internal date, their stated commit and their rough edges are left
exactly as written, because a corrected evidence document is no longer evidence. The
provenance and the staleness are recorded here instead.

Several entries in `CHANGES-linux.md` exist only because of these rounds, item 43 —
correcting an attribution of mine — most of all.

---

## `2026-11-21-optimization-handoff.md`

The fuller record, and the one to read first. A Biomni sandbox round against T7
`NC_001604`, a 4-strain comparative cohort, and a user's real *Salmonella* phage with a
cryo-EM map.

Tested `2cb9ed1` (v1.2.1+linux). Acted on in `be5aaed`, `c04357b` and `bbd10f1`:

| | status |
|---|---|
| P0-1 Mode B silently loses family T | **fixed** — item 45 |
| P0-2 part-name false positive | **done and then calibrated on real data** — items 48, 50 |
| P1-1 `--vocabulary unified` regression gap | **open** — needs the multi-strain sweep. Its first gold-standard data point is item 44, and it went against `unified` |
| P1-2 `run_result.json` conditional key | **documented, not changed** — item 46 |
| P2-1 pharokka upstream | not this repository; carried in the limitations list |
| §3 documentation revisions, all six | **done** — items 43, 47 |
| §5 structure gold standard | **in both skill packages** — item 47 |
| §6 red lines | respected; the one question they raised is item 48 |

Paths in §0 (`/mnt/results/...`, `/workspace/...`) are Biomni-side and do not resolve
here. They are kept because they identify which run produced which number.

## `2026-11-19-test-report.md`

An earlier **draft**, and it says so: `§4.1 工具缺陷` reads "（待补）", `§5 优化建议清单`
appears twice, and `§4.4` lists the real-genome test as not yet run. It records phold as
1.2.6 where the handoff says 1.2.5.

Kept anyway, because it holds detail the handoff only summarises — the step-by-step
evidence ladder, the environment versions, and the record of how the attribution in
item 43 was falsified, verified from `run_result.json` line by line:

```
no database        Primary 0      gp17 R3-E1  G          limited
+ S (RefSeq)       Primary 2      gp17 R3-E2  G+S        limited
+ S + T            Primary 2      gp17 R3-E3  G+S+T      partial
+ --foldseek-all-cds  Primary 2   gp17 R3-E3             partial   (pool 21->49, A2 resolved)
phold + S + T      Primary 3      gp17 R2-E3  D+G+S+T    <- D is what moves it
```

It predates: the full-stack route, the real *Salmonella* genome, the cryo-EM comparison,
the Mode B defect, and everything about presentation signals.

---

## Regression baselines

From the handoff §4. The actionable part of these documents, and the thing to check after
any change that could move a tier.

```
T7, full stack      Primary = {CDS_0039, CDS_0040, CDS_0052}
01F2, full stack    Primary = {CDS_0010, CDS_0011, CDS_0012, CDS_0019}
                    Rescue  = {CDS_0026}
                    (pharokka numbering; under prokka the same proteins are
                     CDS_0008/0009/0010/0016 and CDS_0023)
architecture        01F2  B_myo_baseplate (high)
                    T7    E_jumbo_or_unresolved (low)
repository tests    233 at v1.2.4+linux  (196 when these rounds ran)
```

Reproduce with the commands in the handoff's §4.

Two things about that architecture line are worth stating, since a reader will notice
them:

* **T7 typed as `E_jumbo_or_unresolved` is wrong** — T7 is a podovirus — and it does not
  matter. Architecture is a soft prior reaching `k_default` and the priority loci only;
  there is no code path from it to nomination, so a wrong type cannot exclude a
  candidate. `architecture_is_soft_prior` says so and this round demonstrates it: T7's
  Primary set is correct under a wrong type. A confidence of `low` is the typing
  reporting its own failure rather than hiding it.
* **01F2 typed `B_myo_baseplate` at high confidence** is right, and was a side benefit of
  the full-stack route rather than something asked for.

## Numbering, and why the two schemes both appear

01F2 was annotated twice, by prokka and by pharokka, and **CDS ids are not interchangeable
across annotation sources** — re-annotating changes the gene calls, so `CDS_0016` under
one caller is not `CDS_0016` under the other. The round measured that the *candidate set*
is the same either way (phanotate and prokka disagree on some start codons without
changing which proteins are nominated), which is the useful finding; the id mapping above
is how to read one report against the other.

The cryo-EM table in the handoff §5 uses **prokka** numbering. The regression baseline
above uses **pharokka**. Both are labelled in place.
