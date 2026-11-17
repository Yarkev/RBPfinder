# Handoff — RBPfinder Linux/Biomni build

**Authoritative source:** `github.com/Yarkev/RBPfinder`, branch `claude/loving-gauss-cl7oca`, commit **`8470a1f`**
**Version:** `1.2.1+linux` (package) / `1.2.1-linux` (rules)
**Tests:** 100 passing · CI on Linux + Windows, Python 3.9 + 3.13
**Full change log with rationale:** `CHANGES-linux.md` (31 items)

> Older `/mnt/results/RBPfinder-linux*` artefacts and `verifier_patch.diff` are superseded.
> Do not apply them over this branch.

---

## 1. What this build is

A Linux/Biomni build of RBPfinder: rule-based, interpretable shortlists of candidate
receptor-binding proteins in tailed dsDNA phage genomes.

**Remote submission is refused outright.** `--allow-remote`,
`--data-policy remote_allowed` and `--stage2-backend ebi` exit 2 before any ingest.
Sequences never leave the machine. Valid modes: `private` (default), `import_only`.

**Division of labour, enforced by the interface:** Biomni annotates (pharokka, and phold
only when the user asks for the accurate route); RBPfinder classifies and is the only
thing that decides what is an RBP. RBPfinder does not annotate, so a pharokka or PHROGs
upgrade cannot quietly move a tier.

---

## 2. Deploy

### Sandbox (once per machine)

```bash
RBP_BACKUP=/shared/rbpfinder bash packaging/biomni/setup_biomni.sh
source /workspace/rbpfinder.env
rbpfinder doctor            # expect: Local Stage 3  AVAILABLE
```

Idempotent. `/workspace` survives hibernation; on an OOM rebuild the script restores the
databases from `$RBP_BACKUP` instead of re-downloading.

### Skill package — **needs redeploying at this commit**

```bash
mkdir -p /mnt/results/skills/rbpfinder/references
cp packaging/biomni/skill/rbpfinder/SKILL.md        /mnt/results/skills/rbpfinder/
cp packaging/biomni/skill/rbpfinder/references/*.md /mnt/results/skills/rbpfinder/references/
python3 /mnt/skills/system/phylo-create-skill/scripts/check_skill.py /mnt/results/skills/rbpfinder/
```

The frontmatter keeps id `skill_a75c124d884248a6a599dad14c7f4b24`, so this **updates in
place**. The repo copy under `packaging/biomni/skill/` is the single source of truth;
`/mnt/results/` is a deployment. Edit in the repo and redeploy, or the registered skill
and the code it drives will drift.

Changed since the version currently deployed: the `--foldseek-all-cds` entry (it is not a
no-op on the result, and it carries the pilot numbers) and the Troubleshooting row that
still contradicted it.

---

## 3. Verified vs not verified

### Verified against real components

Real foldseek binary (v9 `39b02b9`, then v10 `941cd33`), official PDB database with
taxonomy, real ProstT5 weights, T7 (RefSeq NC_001604) and 6 SMA strains end to end.

| | Result |
|---|---|
| Column contract (12 columns, no `prob`) | real `easy-search` produces output; `prob` reproduces the `query_ca` crash |
| Positional parsing of foldseek output | every field lands correctly on real data |
| NCBI-style locus tags | accepted, numbered by genome coordinate order |
| Circular topology auto-detection | `topology: circular` honoured without `--circular` |
| Exit-code contract | malformed and missing input both exit 2 with a clean message |
| Taxonomy check | accepts the official single `<prefix>_taxonomy`; refuses a database with neither shape |
| A2 audit | runnable on the all-CDS path; `missing_genomewide_T_or_M` gone, coverage `reduced` → `full` |

### Pilot benchmark (6 labelled strains, two arms, 12 runs)

```
false-complete        0 of 12 runs, both arms          PASS
complete-recall@K     identical both arms              PASS
                      cr@3 9/15 · cr@4..6 10/15
reopen outcomes       362 downgraded vs 2 promoted     PASS
hard negatives        W073 ceilings held both arms     PASS
```

### Not verified

- **The other 42 strains.** No reference labels, so only no-regression and false-complete
  are checkable there.
- **Anything the 6 labelled strains do not cover**, including the pool roughly doubling on
  the all-CDS path.
- **Windows.** This build's structure path is Linux-only (Foldseek has no native Windows
  build).

---

## 4. The single highest-value open action

**Configure a phage sequence database.** `rbpfinder database configure phage <prefix>`.

Every run so far has been in evidence starvation: `doctor` reports
`Local Stage 2 UNAVAILABLE`, so the **S family does not exist**. S is what carries
*functional* homolog annotations, and functional terms are what lift a candidate above
apparatus membership.

Measured consequence: T7 gp17, the literature-established RBP, comes out
`R3 / E2 / unresolved / supports_apparatus_membership` **even with a strong structural
hit**, because every PDB title for it is a part name ("tail fiber protein") or a
crystallographic phrase ("carboxy-terminal domain of … fibre gp17"). Family T alone
cannot fix that; a functional homolog annotation can.

This changes no code and needs no benchmark. It is the largest available improvement to
the original complaint that results were imprecise.

**Ordering note:** running the vocabulary-unification benchmark *before* this is
configured would measure its effect under evidence starvation and could give a wrong
answer.

---

## 5. Open items, by who can do them

### Needs the Biomni environment

| | |
|---|---|
| Configure DB_PHAGE | §4 — highest value, no code change |
| Redeploy the skill package | §2 — the deployed copy predates this commit |
| Run the remaining 42 strains | optional; only no-regression + false-complete are checkable |

### Needs a benchmark to proceed safely

| | What it is |
|---|---|
| **Vocabulary unification** | `rbp_ontology`, 90 terms, shadow state. The reconciled version of the stage2/stage4 divergence. Moves tiers. |
| **`decorrelate` strength-first** | A family's representative is chosen by strength, so a `weak` receptor-binding record loses to a `moderate` apparatus one (T7 CDS_0052). Strength-first is the documented primary key, so changing it is a scientific decision. |
| **The deeper de-correlation issue** | One record per family carries *both* "this family counts" and "what this family supports", so an intra-family conflict cannot be expressed — even though `stage3.af3_priority` treats such a conflict as a P1 trigger. Structural change. |

The two-arm comparison method from the pilot is reusable for all three. Recommend one at
a time.

### Needs a decision from the maintainer

| | |
|---|---|
| **Licence (review I6)** | `All rights reserved` on a public repository: readable and clonable, legally not reusable. If community use or citation is the goal, this needs an open licence. |
| **`KNOWN_ISSUES.md` is Chinese** (I5) | Every other document is English, and this is the file users most need. Translation is mechanical but substantial. |
| **`cli.py` is ~1300 lines** (S1) | Splitting the Stage 0–7 orchestration out would help testability. Large diff; better after the scientific items settle. |
| **M family is behind the field** (S3) | C6 hooks PhageRBPdetect / DepoScope / SpikeHunter, all `calibration: pending`. PLM and structure methods have moved on. |

---

## 6. Traps — do not "fix" these

Each is load-bearing, each looks like an inconsistency, and each now has a test that fails
if it is reverted.

**`S6-A2` is deliberately absent from the context channels** (`evidence/normalize.py`).
A2's premise is that positional signal is *negative*; minting an
`apparatus_membership` context record for an A2 hit would assert exactly what A2
established is absent, and would hand the CDS a free G family — raising `tier_e` for
having been reopened, which `stage6_recovery_confers_no_tier_advantage` forbids. `S6-A1`
and `S6-A3` *are* present because they are positional by construction, so a context
record restores parity with C1 rather than granting a bonus. **Do not add `S6-A2` for
symmetry.**

**`tail fiber` is judged oppositely by two vocabularies, on purpose.**
`stage4.direction_map` reads it as receptor-binding on a CDS's own product name;
`stage2.annotation_term_map` reads it as `apparatus` on a homolog or structure hit. It
was *removed* from `positive_rbp_terms` in 2026-08 with a measurement: on W073 the Ig
adapter rod, the real RBP, the connector and the tip all have homologs named "tail fiber
protein", and keeping it there lifted three standing hard negatives past their ceilings.
"Receptor binding needs a FUNCTIONAL term, not a part name." `tailspike` by contrast *was
added*, because it names one protein that both sits in the tail and binds. **Do not
re-add `tail fiber` to the positive terms** — that was tried and measured worse. The
reconciled fix is `rbp_ontology`, still shadow.

**`prob`, `lddt` and the tmscore columns must never return to the column contract.** A
ProstT5 query is a sequence with no backbone coordinates, so `easy-search` dies on
`query_ca` (foldseek issue #305). `probability_pct: null` is the expected state on this
route, not a fault.

**Taxonomy has two valid shapes.** The official download ships a single
`<prefix>_taxonomy`; a hand-built database needs the four `.dmp` files. Requiring only the
`.dmp` set — which an earlier version did — refuses the official database with exit 2
before any search runs.

**The ML branch is dormant, not broken.** `ml_results` has no producer: the rules specify
a C6 external-ML channel and the M family, but nothing implements them. The branches in
A2 and in the capability probe are written and tested so that wiring C6 is a wiring job. A
test asserts the dormancy and fails when a producer appears.

---

## 7. Operating notes worth carrying forward

**A fake binary validates plumbing, never a contract.** The local Foldseek route passed
its tests against a fake foldseek that emitted whatever columns it was asked for. The
real binary then crashed on the first column the contract requested. Two defects shipped
this way before real-data validation caught them.

**Do not write an unverified inference as a verified fact.** A source comment claimed the
official download ships the `.dmp` taxonomy set. It does not, the claim had never been
checked, and the resulting check refused the real database — a worse failure than the
false positive it replaced.

**Opening a gate means checking what is behind it.** Making the A2 probe able to return
True walked into an audit body that had never been written, behind a guard that said so.
An honest "unexecutable" became an exit-1 crash.

**"Changes no conclusion" needed measuring, not reasoning.** `--foldseek-all-cds` was
described as changing no nomination or tier. A2 reopens the pool, reopened CDS are
classified, and Primary is topped up with the strongest R2 — so Primary can change, and
did. The error was conflating "the tier a CDS earns from given evidence" with "the
candidate set".

**The working pattern:** changes here, verification in the real environment there. It
caught every one of the above. Keep it for anything touching an external tool contract.

---

## 8. Reading order for a new agent

1. `packaging/biomni/skill/rbpfinder/SKILL.md` — the operating contract, including how to
   read direction labels, what an empty Primary means, and what to do when known biology
   disagrees with the output.
2. `packaging/biomni/skill/rbpfinder/references/KNOW_HOW.md` — the procedure and the
   failure modes it prevents.
3. `CHANGES-linux.md` — every change with its evidence, including the three defects
   introduced and then corrected during this work.
4. `packaging/skill/rbpfinder/references/KNOWN_ISSUES.md` — §10 (local Foldseek, validated)
   and §11 (why T7 gp17 reads R3/apparatus). Chinese; see §5.
5. `rules/decision_rules.yaml` — the judgment itself. Nothing in `rbpfinder/*.py`
   hardcodes a threshold.
