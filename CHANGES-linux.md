# CHANGES — Linux/Biomni build (1.2.1+linux)

Everything in this build relative to upstream v1.2.0 (`529d177`). Two sources: the
2026-11 external code review, and defects found by probing the tier logic directly.

Every item below is covered by a test in `tests/` (73 tests, run on Linux and Windows
against Python 3.9 and 3.13 by `.github/workflows/tests.yml`).

---

## Correctness fixes

### 1. C1 now honours circular adjacency (upstream blocker)

`decision_rules.yaml` requires `circular_adjacency` for `c1_window`, but
`c1_context.run()` took no circularity argument and clamped windows to `[0, n-1]`. A tail
module spanning the origin produced two truncated windows that the linear merge
(`lo <= hi + 1`) could never join. C4 and the A3 audit already honoured the rule; the
most important positional channel did not.

Fixed by rotating the coordinate system so the cut falls in the largest circular gap
between anchors — no anchor cluster straddles the cut, so the tested linear algorithm
runs unchanged in rotated space and the result is mapped back mod n. **The linear path
is byte-for-byte the previous behaviour.**

Measured on a 20-CDS genome whose tail anchors sit at the origin: 8 CDS nominated as
linear, **15 as circular**, the extra 7 being the origin-adjacent dark-matter CDS that
the linear channel structurally could not reach.

### 2. The resolved circularity now reaches the channels

Fixing (1) alone left it dormant. Two further defects:

- `cli.py` passed `is_circular=args.circular` into the reader. The flag defaults to
  `False`, not `None`, so it **overrode** the reader's own `topology: circular`
  detection — a record declaring itself circular was analysed as linear.
- `meta["is_circular"]` reported the raw flag rather than the resolved value, so nothing
  downstream could see the real answer.

Now the reader resolves topology (with `--circular` as an explicit override), reports it
in `meta`, and **C1, C4, C5 and the A3 audit all read that one resolved value** — the
rule file requires circular adjacency for three of them.

### 3. Annotated GenBank files other than pharokka's are accepted (upstream blocker)

Ingest hard-failed on any locus tag not ending in `CDS_<number>`, which rejects NCBI
RefSeq records — the most common public input. Now such tags are accepted: the CDS are
numbered **in genome coordinate order**, starting above the highest explicit number so
the two schemes never collide, the original tag is preserved verbatim in
`raw_locus_tag`, and each assignment is reported in `meta["warnings"]`.

Coordinate order, **not file order**: file order is a property of the file, so a
re-export with features reordered would renumber every protein, and `cds_id` is the key
that `all_CDS_screening.tsv`, every manifest, the search state and `--stage2-manifest`
replay are written against.

Still hard failures: a CDS with no `/locus_tag` and no `/ID` (nothing to preserve), and
duplicate **explicit** CDS numbers.

**Limitation, stated plainly:** ids assigned this way change if the genome is
re-annotated with a different caller, because the CDS set itself changes. Artefacts must
not be mixed across annotation sources.

### 4. Input errors now obey the documented exit-code contract

`GenBankIngestError` derived from bare `Exception`, so a malformed input escaped as a raw
traceback with **exit 1** — "unexpected internal error" in the contract SKILL.md gives
agents, which would have an agent report a tool defect instead of telling the user to fix
their file. It now derives from `InputValidationError` (**exit 2**, clean message), which
is what `errors.py` was written to enforce: it names two error domains precisely to stop
a third shape appearing.

A missing input path was the same shape (bare `FileNotFoundError`, exit 1) and is now
checked explicitly for every input flag.

### 5. A family's direction no longer depends on tool names sorting alphabetically

`decorrelate()` picked each family's representative with
`key=(strength, tool)`. The representative carries the family's **direction** into
tiering, so at equal strength the alphabet decided a scientific conclusion: `hhpred`
sorts before `pharokka_phrog`, so an apparatus reading vetoed an equal-strength
receptor-binding one — the veto `evidence_families.dual_role_precedence` explicitly
forbids ("a structural name may never veto a positive binding result").

Measured: the same protein with the same evidence came out **R3-E3 or R2-E3 depending
only on whether the competing tool's name sorted earlier**.

The tie-break is now an explicit, YAML-defined order
(`evidence_families.representative_direction_precedence`): a positive non-RBP assignment
is never hidden, receptor binding is never vetoed by a structural reading, and the tool
name remains the final key so the result stays deterministic. Strength still decides
first.

**Not fixed, and recorded in the rules:** one record per family still carries both "this
family counts" and "what this family supports", so a family holding both a
receptor-binding and a non-RBP record cannot express that conflict — even though
`stage3.af3_priority` treats such a conflict as a P1 trigger. Splitting the two questions
needs the benchmark.

---

## Capability added

### 6. Local Foldseek structure search (family T), Linux only

Fills the `local_foldseek` slot that `data_policy.provider_registry` and
`capability_matrix` already declared (`executor: not_bundled`). Foldseek with the ProstT5
weights searches an amino-acid query against a local structure database directly — no
AlphaFold step, no web handoff, nothing transmitted, no tokens spent driving a website.

This is what makes the top tier reachable: R1 requires a direct evidence family
(T/D/S), so without structural evidence a genuine RBP stalls at R2/R3. Results are
normalised into the shape the existing Foldseek parser returns and registered under
manifest kind `foldseek_json`, so `structural.build` stays the single interpreter for
both this route and the manual-import route.

Runs over the **nominated candidate pool** by default, not every CDS: structure is
candidate-scoped everywhere else in the product, and ProstT5 is CPU-bound without a GPU.
`--foldseek-all-cds` forces genome-wide.

Validated against a real foldseek binary and the official PDB database on
2026-11-16; that validation found four defects, fixed in items 13-16 below.

### 7. `rbpfinder validate-input`

Answers the precondition question in seconds, using the same readers the run uses, so an
orchestrator can check an annotation **before** spending an hour on phold or on a run
that cannot work. `--json` for machine consumption.

### 8. Biomni integration (`packaging/biomni/`)

A skill package (`skill/rbpfinder/SKILL.md` + `KNOW_HOW.md`) for
`/mnt/results/skills/<slug>/`, which is what the hosted platform registers and what
`check_skill` validates, plus an idempotent `/workspace` setup script. Biomni annotates
(pharokka, and phold only when the user asks); RBPfinder classifies.

An earlier iteration shipped an `agent.add_tool()` Python layer, written from the
open-source Biomni `A1` source. That API is **not present in this platform's installed
`biomni.tool`** — it raises `AttributeError` — so the layer was removed rather than left
as dead code.

---

## Vocabulary fixes

### 9. `Tal` and `Dit` are matched as whole tokens in C1 and C2

As plain substrings they made "me**tal**lopeptidase", "ca**tal**ytic subunit" and
"RNA e**dit**ing protein" STRONG tail anchors, each opening a full window inside a
replication module, and nominated every "por**tal** protein". They now use the same
`token_match_terms` list the homolog reader already used.

### 10. Helper head nouns resolve to `structural_only`

`stage4.direction_map` held no helper terms, so "tail fiber assembly protein" read as
receptor-binding and reached Primary — despite being the documented
`compound_title_helper` regression class (T4 P03739, lambda P03740 are chaperones).

---

## Build policy

### 11. Remote submission is disabled

`--allow-remote`, `--data-policy remote_allowed` and `--stage2-backend ebi` are refused
with exit code 2, before any ingest or provider code runs. `DataPolicy` remains as a
second, independent gate. Valid modes: `private` (default) and `import_only`.

On a server that can download Foldseek, a structure database and a BLAST database, every
evidence family is reachable locally — including HHpred and InterProScan, whose
"manual only" status upstream is a **Windows** limitation, not a Linux one — so nothing
is given up by refusing to transmit.

### 12. Versions aligned

`pyproject.toml` (1.2.0) and `meta.version` (1.0.0-spec) disagreed, which undercuts the
promise that a result can be traced to the rules that produced it. Both now read
1.2.1+linux / 1.2.1-linux. `__init__.py` already read the installed distribution
metadata and needed no change.

---

## Fixes from real-binary validation (2026-11-16)

Items 6–8 above had been validated only against a *fake* foldseek binary, which emitted
whatever columns it was asked for. That proved the plumbing and could not prove the
contract. Running a real binary against the official PDB database (and T7, RefSeq
NC_001604, end to end) found four defects.

### 13. The column contract made every real search crash (P1)

`stage3.local_foldseek.format_output` requested `prob`. A ProstT5 query is a sequence and
has no backbone coordinates, so foldseek dies with
`No datafile could be found for .../query_ca! Error: Convert Alignments died`.
Upstream confirms it (steineggerlab/foldseek issue #305: "ProstT5 can't be combined with
scores that require structure information"). `prob`, `lddt` and the tmscore columns are
now excluded from the contract and from the executor's fallback list, with the reason
recorded in both. A test asserts no coordinate-only column can reappear.

Dropping it is a safe degradation: `structural.consensus` already keeps a hit whose
probability is None rather than filtering it.

### 14. The schema then rejected the result it had just produced (P2)

With `prob` gone, `probability_pct` is null — and the schema declared it `number`, so the
run failed validation and exited 2. `probability_pct` now admits null, which is the
expected state on this route, not a fault.

The exit-2 message now says a schema failure is an **internal** contract violation rather
than a problem with the user's input: the code is shared with input errors, and a reader
who saw only the code would tell the user to fix a file that is fine.

### 15. The A2 audit was structurally blind to local structure search (P3)

Two independent defects, both needed fixing:

- The capability probe read only `tophit_protein`, which nothing but a phold TSV import
  ever fills. A run that searched all 60 CDS and found 48 hits still reported
  `not_executable_due_to_missing_genomewide_T_or_M`. The probe now takes
  `structural_tophits` as a second source — passed **only** when the search covered the
  whole genome, because A2 asks which CDS have structural signal and no positional
  signal, and a pool-scoped search cannot answer that.

  This item originally claimed `--foldseek-all-cds` "keeps its promise to change no
  conclusion: it changes what the audit can RUN, never the pool or the tiers". **The
  second half is false, and the pilot benchmark disproved it** — see item 23.
- The structure-database whitelist required an underscore directly after the 4-character
  id, while the official PDB download emits `7ung-assembly1_o1`. All 8 real hits failed
  it. The pattern now has an `<4char>-assembly<n>[_chain]` branch; `WP_*`, `envhog_*`,
  `protein<N>` and the rest of phold's sequence-derived namespaces still fail it, so a
  sequence datum still cannot be counted in both D and T.

### 16. doctor called a database AVAILABLE that killed every search (P5)

A structure database built by hand has no taxonomy files, and easy-search dies at run
time when `taxname` is requested against it. doctor reported `AVAILABLE`. When the column
contract asks for `taxname`, the resolver now checks `<prefix>_mapping`,
`_nodes.dmp`, `_names.dmp` and `_merged.dmp` and reports `CONFIGURED, INVALID` naming the
remedy. The check is tied to the contract, not hardcoded.

### 17. An undeclared topology is now reported (P4)

A record with no `/topology` is read as linear — correct, but silent in a way that
matters. On a circular genome whose tail module spans the origin, C1 and C3 lose their
nominations there while the **pool size does not change**, because C4's ±2 neighbourhood
recovers the same CDS by another route. Measured on a rotated T7: CDS 0044/0045 fell from
`C1+C3` to `C4` alone with nothing in the printed output revealing it.
`validate-input` now warns when a record declares no topology.

### 18. What real data confirmed was already right

`_row_to_alignment`'s positional parsing landed correctly on every field, including the
`"<identifier> <description>"` shape produced by joining `target` and `theader`.

---

## Corrections from the verification round (2026-11-16, second pass)

Two of the fixes above were wrong, and both were found by running the real thing again.

### 19. The taxonomy check refused the official PDB database (regression in item 16)

Item 16 required `<prefix>_mapping`, `_nodes.dmp`, `_names.dmp` and `_merged.dmp`, on the
strength of a source comment asserting that `foldseek databases` shipped them. **It does
not, and the assertion had never been verified -- it was an assumption written as a
fact.** The official download ships a single `<prefix>_taxonomy` file (741 MB for PDB),
which a real run had already resolved `taxname` from.

The result was worse than the false positive item 16 removed: the official database was
refused with exit 2 before any search could run. The check now accepts either shape --
the single file, or the four-file `.dmp` set a hand-built database would need -- and
refuses only a database with neither.

The four `.dmp` names came from the error message a hand-built database produced. That
names what foldseek *looked for*, not what a valid database must *have*; the two were
conflated.

### 20. Enabling the A2 probe walked into an unimplemented scan (incomplete item 15)

Item 15 made the A2 capability probe able to return True for the first time. The audit
body behind it had never been written, and a guard stood there for exactly this case:

    if a2_ok:
        raise NotImplementedError(
            "A2 became executable (a genome-wide T/M source appeared) but the scan is
             not implemented; refusing to report A2 as passed")

So the fix turned an honest "unexecutable" into an exit-1 crash. The guard was doing its
job; the mistake was enabling the gate without checking what lay behind it.

`a2_context_negative_signal_positive` now implements the question the rules state: CDS
outside pool P carrying a hit the structure-database whitelist accepts, plus ML
nominations where present. A hit reopens the pool and assigns nothing; whether a reopened
CDS earns a tier is the ordinary classify pass's answer, and it is free to come back
unchanged.

Verified end to end on T7 with `--foldseek-all-cds` against the real PDB database:
`not_executable_due_to_missing_genomewide_T_or_M` is gone, assessment coverage moves from
`reduced` to `full`, A2 flags 28 CDS, one reopen cycle, all reopened candidates come back
`reopened_and_downgraded` (honestly low, nothing inflated), terminated cleanly, schema
PASS, exit 0.

**Measured since:** the pool grows from 21 to 49 CDS on that run. That is the designed
reopen-plus-cap mechanism working; the pilot benchmark then measured its effect on six
labelled strains — see items 23-26. The full 48-strain regression is still not run.

### 21. The A2 ML branch is dormant, and now says so

A2 and the capability probe both accept `ml_results`, and both have a branch for it. No
channel produces it: the rules specify a C6 external-ML channel and the M family, but
`nominate/` has no `c6_*` module and nothing passes the argument, so both branches are
unreachable today. Independently confirmed during verification.

They are kept, tested and documented at both sites rather than deleted, so that wiring C6
is a wiring job and not a design job. A test asserts the dormancy itself -- it fails if a
producer appears, which is the signal to remove the notes.

### 22. The know-how reference was stale

`references/KNOW_HOW.md` still described a Python API (`rbpfinder_workflow_plan`,
`check_annotation`, `run_rbpfinder`) that was removed when the `add_tool` layer went, told
the agent that locus tags must end in `CDS_<number>`, and described remote EBI BLAST as
available-but-off. All three were wrong for this build, and the file had been deployed.
Rewritten against what the build actually does.

---

## Pilot benchmark (2026-11-17), and one claim it disproved

Six strains carrying reference labels, two arms (default pool-scoped vs
`--foldseek-all-cds`), same input, same foldseek build, same PDB database, 12 runs.

### 23. `--foldseek-all-cds` is not a no-op on the result

Items 6 and 15, and the flag's own help text, said it changes no nomination or tier. That
was my reasoning, not a measurement, and it was wrong in a way worth naming: I conflated
"the tier a CDS earns from a given evidence set does not change" with "the candidate set
does not change". A2 **reopens the pool**; reopened CDS are then classified like any
other; new candidates appear; and `stage7.candidate_set.primary_set` says Primary is
"R1 candidates, topped up with the strongest R2". So Primary can change — measured on
N136, 1 candidate to 3.

Corrected in the help text, the code comment and the skill. The accurate statement: the
tier a CDS earns from given evidence is unchanged, and the reopen provenance contributes
no evidence family, but the pool and therefore the reported sets can change.

### 24. The three metrics passed

    false-complete        0 in 12 of 12 runs, both arms
    complete-recall@K     identical in both arms (cr@3 9/15; cr@4..6 10/15)
    reopen outcomes       362 reopened_and_downgraded vs 2 reopened_and_promoted

Reference-RBP ranks barely moved despite pools roughly doubling: only N136 CDS_0101
13 -> 18 and C277 CDS_0223 83 -> 84, both already outside every K. The W073 hard
negatives held their ceilings in both arms (CDS_0004 R3, CDS_0006 R2, CDS_0030 R3).

### 25. Why the two promotions are correct, and why a reopen cannot inflate a tier

A2 promoted two N136 CDS into Primary. That is documented behaviour, for two independent
reasons:

- **R2 in Primary is by design.** `primary_set.contents` is "R1 candidates, topped up
  with the strongest R2", typical size 2-4. N136's default-arm Primary held 1 candidate,
  *below* that range; the all-CDS arm gave 3, inside it. The top-up rule did what it says.
- **The reopen conferred no tier advantage**, which
  `evidence_families.stage6_recovery_confers_no_tier_advantage` requires ("it contributes
  nothing to tier_r or tier_e"). `normalize.build` mints a genomic-context record for
  `S6-A1` and `S6-A3` but **not** for `S6-A2`, so an A2-reopened CDS gains no family from
  having been reopened and has to earn its tier from its own evidence.

  That omission is right on principle, not merely convenient: A2's premise is that
  positional signal is NEGATIVE, so minting an `apparatus_membership` context record for
  an A2 hit would assert exactly what A2 established is absent. A1 and A3 are positional
  by construction, so giving them a context record restores parity with C1 rather than
  granting a bonus. The asymmetry is now documented at the call site.

### 26. The change converts "cannot verify" into "verified"

On the default path all six strains reported `terminated_cleanly: false`, with A2 stating
precisely why it could not run. On the all-CDS path all six terminated cleanly. That is
the value of the change, and it is the honest framing: it does not make the answer better,
it makes the completeness claim checkable.

**Still not measured:** the other 42 strains, and anything the 6 labelled strains do not
cover.

---

## Review debt cleared (2026-11-17)

### 27. The add-only invariant is now actually asserted (review I4)

`invariants.nomination_is_add_only` declared
`enforcement: assert len(P_after_channel) >= len(P_before_channel)` and no such assertion
existed anywhere. The invariant held, but only as a property of the implementation -- the
pool is a set and every channel only adds -- and a property is not a contract: the first
channel to grow a filter would have broken it silently while this file went on claiming a
gate.

Asserted per channel in `cli.run`, so a violation names the channel that shrank the pool.
`--suppress-nomination` is the one sanctioned removal and is now named as an exception in
the rules: it is an operator request made after every channel has run, which is why the
assertion sits before it.

### 28. `standing_authorisation` is out of the rules file (review I2)

It recorded that one project had been granted remote submission on a date -- a run
record, not a rule -- and nothing in the code ever read it. Two reasons it does not belong
there: a reader, above all an agent driving the tool, can take `granted: true` for a
default authorisation; and a grant made to one project does not travel with the file to
whoever clones it. Authorisation is per-run only, and in this build remote is refused
outright.

### 29. Dead code in the tier assignment (review S2)

`rb_families != ["G"]` was unreachable: `direct_families` is `rb_families` intersected
with {T, D, S}, so a non-empty `direct_families` already rules out G-only support. And two
branches computed R3 separately, reading as though they distinguished something; they did
not. Merged, with R3 documented as the floor rather than a classification.

Verified behaviour-preserving by exhausting all 16384 combinations of
(rb_families, independent_families, apparatus, hard_contradiction) against the previous
expression: zero differences.

### 30. The installers say what they are for (review B4)

`install_linux.sh` and `install_windows.ps1` install from a wheel in `runtime/`, which the
release bundle ships and the repository does not. Run after `git clone` they failed with a
bare "no rbpfinder wheel found", which reads as a broken installer rather than the wrong
one. They now explain the distinction and point at `pip install .` or, on a server,
`packaging/biomni/setup_biomni.sh`. README leads with the Biomni setup and marks the
platform installers as bundle-only.

### 31. The dangling `scripts/` references say they are dangling (review B3)

`KNOWN_ISSUES.md` cited internal gate scripts that are not shipped. The citations are kept
-- they record that a gate existed and what it caught -- with a note that `scripts/` is
not in this repository and the public tests are in `tests/`.

---

### 32. The database registry now lives where it survives (2026-11-17)

`rbpfinder database configure` writes to `$XDG_CONFIG_HOME` or `~/.config`. On the Biomni
sandbox that path is not in the persistent snapshot, so a hibernate/resume silently lost
the DB_PHAGE registration **while the database files themselves survived** — `doctor` went
back to `NOT CONFIGURED` and every subsequent run was in evidence starvation again, with
nothing pointing at the cause. Reported from the real environment.

`setup_biomni.sh` now sets `RBPFINDER_CONFIG` to a path inside the prefix, which
`database.config_path()` already honoured, so the registration is durable without any new
mechanism. The script's env file also documents the `RBPFINDER_DB_PHAGE` /
`RBPFINDER_DB_HOST` alternative, which takes precedence over the registry, and says why
family S matters. Two tests pin the override and the script's use of it.

This closes the loop on the original complaint. With DB_PHAGE configured, T7 gp17 moves
from `R3`, families G+T, outside Primary — to `R2`, families **D+G+S+T**, rank 3, in
Primary.

> **The attribution in the next sentence was wrong, and item 43 corrects it.** It read:
> "The cause was evidence starvation, as diagnosed, not the vocabulary." A Biomni test
> round could not reproduce the promotion from a sequence database alone, and the
> vocabulary divergence turns out to be exactly what produces it.

---

### 33. The three vocabularies, measured before anything was changed (2026-11-18)

Item 32 took the gp17 failure to be evidence starvation rather than vocabulary — wrongly,
as item 43 records; it is both, and the divergence is the load-bearing half. That reading
removed the urgency but not the divergence, so the divergence was measured rather than
assumed. Three
readers answer *does this text support receptor binding?* from three different maps:

| | reads | covers | disagrees with `rbp_ontology` on |
|---|---|---|---|
| `stage2.annotation_term_map` | homolog / structure hit descriptions | 63 of 90 terms | **1** (`baseplate wedge`, non-binding either way) |
| `stage4.direction_map` | a CDS's own product name | 44 of 90 | **3** (`tail fiber`, `tail fibre`, `tip attachment`) |
| `rbp_ontology` | nothing — shadow | 90 | — |

Terms neither live map covers: **0**. The ontology introduces no new vocabulary; it is a
reconciliation. The incremental fixes (`tailspike` promoted, `lectin` removed) had already
closed most of the gap, so the scientific question is now a single sentence: *does the part
name "tail fiber protein" support receptor binding when it is the CDS's own product name,
given that it does not when it is a homolog's description?*

### 34. `explicit_rbp_terms` existed in only one of the three readers (2026-11-18)

Found while preparing the unification, and it would have **regressed N4 gp65** had the
ontology been adopted without it:

```
                                            stage4   stage2   ontology
tail sheath and receptor binding protein    RB  ✓    struct   struct
```

`evidence_families.dual_role_precedence` states the rule as a veto — *"a structural name
may never veto a positive binding result"* — so a homolog *described* like gp65 was being
demoted on two of three paths, not just an edge case in the product-name reader. The guard
now lives in `terms.explicit_rbp_claim` and is honoured by `terms.classify` (mode-gated,
so `current` stays bit-identical) and by `ontology.classify` (unconditional, because it is
what makes the ontology adoptable at all).

This is why the comparison has **three** arms: the guard is a separate, rule-mandated
change, and bundling it with the unification would make any regression unattributable.

### 35. `vocabulary.mode` — the unification is selectable, and the default does not move

`rules.vocabulary.mode` ∈ `{current, explicit_guard, unified}`, default `current`, exposed
as `--vocabulary` and recorded in `run_result.json`. `current` reproduces the frozen
behaviour exactly; the new `Rules.set_override` keeps the YAML on disk untouched, so no run
is ever explained by an edited rules file. The YAML block names the forbidden use in as
many words: changing the default to make a particular genome look better.

Measured delta on a 12-CDS probe genome with PHROG annotations (needed — without an
annotation the product-name direction only reaches a weak G record and the change is
invisible):

```
CDS   product                                    current        unified
0004  tail fiber protein                         R2-E2 rank 1   R3-E2 rank 7   ***
0006  tip attachment protein                     R2-E2          R3-E2          ***
0005  tailspike protein            (control)     R2-E2 rank 2   R2-E2 rank 1
0007  gp65-class, explicit claim   (control)     R2-E2 rank 4   R2-E2 rank 2
Primary: [0004, 0005, 0006, 0007]  ->  [0005, 0007]
```

`explicit_guard` produced **zero** differences from `current` here, which is what makes the
rest attributable to the unification alone. Both controls held and the two genuine RBPs
moved **up**. Also measured, and recorded
rather than hidden: `unified` loses the helper-noun distinction (`tail fiber assembly
protein` reads apparatus, not structural_only — non-binding either way, but the role label
changes), and 28 terms gain an opinion the product-name reader did not have.

`unified` deliberately does **not** adopt the ontology's `interpretation_confidence`, which
would move strengths as well as directions. That is a second variable.

### 36. A comparison harness, because this change moves tiers (2026-11-18)

`packaging/comparison/compare_arms.py` runs the same genomes under N arms and diffs tier
changes, Primary membership, reference-RBP rank moves, false-complete and hard-negative
ceiling lifts; it resumes, and it makes no judgement about whether a change is good.
`packaging/comparison/VOCABULARY-COMPARISON.md` is the plan: the arms, the acceptance
conditions, and the risk that decides this.

That risk, stated before the 42-strain run rather than after: **T7 gp17's product name is
"tail fiber protein"**, so `unified` flips its D record to apparatus and may undo the
DB_PHAGE gain from item 32. It holds `R2` only if S or T still carries a functional term.
So the question the benchmark answers is not "did anything change" but *do the reference
RBPs keep their tier and their place in Primary?* — and it must be run with DB_PHAGE
configured, or it measures `unified` against a starved baseline and will look far more
destructive than it is.

The harness is code, not curated data, so it sits in `packaging/comparison/` rather than a
`benchmark/` directory, which `.gitignore` excludes at any depth.


### 37. The desktop skill package advertised a capability the tool refuses (2026-11-19)

Found while auditing the two skill packages, and worse than drift — the desktop
`SKILL.md` asserted three incompatible things at once:

* "Windows is validated. Linux and macOS are not yet validated"
* a full EBI pre-flight protocol: disclose the transmission, collect a contact email,
  require `--allow-remote`
* "not available in this build. This Linux/Biomni build refuses `--allow-remote` …"

The third was edited in by `a774bd1`, which standardised the *server* route and bled into
the wrong package. An agent reading this would have walked a user through an
authorization flow for a path that exits 2 before any ingest.

The same staleness ran through the references: `DATA_POLICY.md` called `remote` the
"default for new installs" and documented the disclosure flow as live; `USER_GUIDE.md`
gave a copy-pasteable `--stage2-backend ebi --data-policy remote_allowed --allow-remote`
command line; `INTERACTIVE_CONFIRMATION.md` listed "InterProScan via its existing remote
path" as part of the automated bulk pass — `interpro_remote` has **no caller left in the
tool**. `OUTPUT_GUIDE.md` still explained exit 2 as "missing EBI email". Outside the
packages, `README.md` and `packaging/README.md` carried the same claim, including an
example prompt telling the user to say *"EBI remote Stage 2 is allowed."*

Three further inconsistencies in the tool itself, which is what let the docs look
plausible:

* `--stage2-backend`'s help text still described `ebi` as submitting sequences "which
  requires remote_allowed plus per-run authorisation". It now says REMOVED. The choice
  stays parseable on purpose, so an old command line gets the explicit refusal and its
  reason rather than argparse's "invalid choice".
* The three refusal messages said "disabled in this build (linux/biomni)" although the
  refusal is **unconditional in the source**. A desktop user would reasonably have gone
  looking for a build that allows it. Now "disabled in RBPfinder".
* The comment above the refusals explained only the server rationale. It now records
  that the refusals apply to a desktop install from the same source, where the cost is
  real, and that the trade-off is documented rather than hidden.

Both packages now state which one they are, point at the other, and say the difference is
not cosmetic: **family T is only reachable inside a run on Linux**. The desktop skill says
plainly that a missing family T there means "not looked for on this machine", not "no
structural support" — the distinction `searched-no-hit is not not-run` already makes
everywhere else.

`tests/test_skill_packages.py` (26 tests) pins all of it: no skill doc may put a refused
flag in a runnable command line, the desktop package may not ask for a contact email,
neither may frame the refusal as this build's doing, and each must point at the other.
Checked retrospectively against `HEAD` — the suite catches **12** of the defects above.

### 38. Correction: three documents are Chinese, not one (2026-11-19)

Item I5 and §5 of the handoff both recorded `KNOWN_ISSUES.md` as the only Chinese
document. Measured properly (counting Han codepoints rather than grepping a byte range,
which matched the em-dashes in every English file): `ADVANCED.md` 25.5%, `KNOWN_ISSUES.md`
29.5%, `USER_GUIDE.md` 28.1%. The translation decision is therefore about three files.

### 39. A run now says which software and which rules produced it (2026-11-19)

The rules file has claimed since v1 that *"a run cites the rules version"*. It did not —
**no version string appeared anywhere in `run_result.json`**, across all 30 top-level
keys. Every benchmark number produced so far is therefore unattributable to a release.

The two version strings had also drifted: the package read `1.2.1+linux` and the rules
read `1.2.1-linux`. Close enough to look right in a diff, different enough that `pip show`
and a run's own citation disagreed — and `-linux` is not a valid PEP 440 local version at
all, so it would normalise to something else again in distribution metadata.

Both fixed. `meta.version` is now byte-identical to pyproject's, a run records
`versions: {package, rules, schema}`, and the package number is read from distribution
metadata rather than retyped, so there is no third copy to keep in step. In an uninstalled
source tree it honestly reports `0+unknown`.

A correction to what I told the maintainer beforehand: I warned that adding a field
mid-benchmark could break comparability through schema validation. That was unfounded —
`evidence_schema.json` has **zero** top-level properties and sets `additionalProperties`
nowhere, and `vocabulary` (item 35) is not in it either and validated fine. Adding fields
cannot break it. `tests/test_version_provenance.py` pins that too, so it stays true.

### 40. The release bundle can actually be built (2026-11-19)

`packaging/README.md` documented a bundle layout, and `install_linux.sh` /
`install_windows.ps1` hard-required it — `runtime/<wheel>`, `runtime/wheels/`,
`VERSION.json`, `SHA256SUMS.txt`. Nothing produced any of it:
`scripts/build_release_bundle.py` was referenced twice by `.gitignore` and **did not
exist**, along with the whole `scripts/` directory. So the installers had never been
executed, and the README would have shipped with `{{VERSION}}` and `{{WHEEL}}` still in
it.

Written, and run. The builder takes the version from the wheel rather than retyping it,
refuses to build when the rules version disagrees, substitutes the template, optionally
downloads dependency wheels for offline install, writes `VERSION.json` and
`SHA256SUMS.txt`, and then **verifies the result against what the installers actually
read** — a missing file or a leftover placeholder fails the build.

The internal-material boundary is enforced by rule rather than by memory, including
`packaging/comparison/`: a user installs the analysis, not the instrument used to decide
what the analysis should do. A file that is neither included nor forbidden stops the
build, because it is one nobody has classified.

Three things the first real build and install exposed:

* **The bundle would have installed the wrong skill on Linux.** It ships
  `install_linux.sh`, but only the desktop skill package, whose own text says "This is
  the desktop / Windows package". Both packages now ship, `install_linux.sh` prefers
  `skill/rbpfinder-linux`, and the builder requires it to be present — the installer's
  fallback to the desktop package would otherwise be a silent wrong-skill install.
* **Both verifiers asserted that the removed capability was advertised**: `--help
  documents the EBI contact email` and `--help documents --allow-remote` were passing
  checks. That is a gate which would have failed the day someone correctly removed the
  flags. They now verify the refusal — `--allow-remote` exits 2 with its message —
  which is the property a user depends on.
* **Both installers closed with** *"Remote Stage 2 asks for authorization the first time
  you use it."* Printed at the end of every successful install.

Measured on a real build: wheel + 18 files, or 26 with dependency wheels; 20 MB archived.
Both install paths were run for real — networked and `--offline` — each ending
`VERIFY_INSTALL=PASS (11/11)`, with the correct Linux skill installed and an end-to-end
run exiting 0 and citing `{"package": "1.2.1+linux", "rules": "1.2.1+linux"}`.

### 41. The acquisition plan offered an authorisation that cannot exist (2026-11-19)

Printed on every run, so the most-read place this could still claim a capability:

```
EBI Job Dispatcher (BLASTP)   AUTO   requested, but this run is not authorised to transmit
```

`not_authorised` is reached whenever authorisation is absent — which is **always**, since
`--allow-remote` is refused before any ingest. Wording it as *this run* invited the user
to go looking for a run that would be authorised. The status code is kept, because other
readers key on it; the prose now says transmission was removed and names the two real
routes. Found by reading the captured output of a test I was writing for something else.

That makes **seven** places this one removed capability was still advertised: the desktop
skill and four of its references, the `--stage2-backend` help text, the refusal messages'
own parenthetical, two READMEs, the acquisition plan, both verifiers and both installers.
The lesson is in item 37: the code was changed in one place and nothing checked that the
things describing it agreed.

### 42. The Biomni skill package has a repeatable build and a local gate (2026-11-19)

`python3 scripts/build_release_bundle.py --biomni-skill-only` emits
`rbpfinder-biomni-skill-<version>.tar.gz`, which extracts to `rbpfinder/` — the layout a
Biomni skills directory expects. It carries **no wheel**: Biomni installs the code from a
checkout via `setup_biomni.sh`, so the skill deploys separately from the runtime, and the
two must be updated together or the skill documents flags the installed code lacks.

The gate runs before the tarball is written, because a rejected upload costs a round trip
and every failure mode is local: invalid frontmatter, a missing `name`, an over-long
description, a reference that does not resolve, a file that ships without being
referenced, and a path that exists only on the machine that built it. Each of those has a
test that confirms the gate *catches* it — a gate that passes everything is worse than
none.

SKILL.md also now tells the agent to quote `versions` and `vocabulary` whenever it reports
a number, and to say so explicitly when `package` reads `0+unknown`, which means the tool
is running from a source tree that was never installed rather than a released version.

## v1.2.2+linux — the Biomni test round

A full test round on the Biomni sandbox (2026-11-19 to 11-21) against T7 `NC_001604`, a
4-strain comparative cohort, and a user's real *Salmonella* phage with a cryo-EM map.
It confirmed all 196 tests, verified all 14 known issues in `WORKFLOW.md` §5 — and found
one blocking defect plus one wrong claim of mine.

### 43. CORRECTION: the gp17 promotion was not the sequence database (2026-11-21)

Item 32 reported that configuring DB_PHAGE moved T7 gp17 from `R3` outside Primary to
`R2`, rank 3, in Primary, and concluded "the cause was evidence starvation, as diagnosed,
not the vocabulary". **The attribution is wrong**, and the conclusion with it.

Measured against a full RefSeq viral protein database (722,823 sequences):

```
gp17 S top-10 hits   ALL annotated "tail fiber protein"  -> apparatus consensus 10/10
                     (including its own NP_042005.1 at 100% identity)
gp17 tier            R3-E1 -> R3-E3        never enters Primary
```

More families raise `tier_e`. They cannot raise `tier_r`, which needs a direction. What
actually produces the promotion is a **D family carrying a receptor-binding reading**:
phold's `phrog:4242` annotation gives gp17 a D record, the D term map reads "tail fiber
protein" as `supports_receptor_binding`, and that one direct family takes it to `R2-E3`
and into Primary.

```
fast      RefSeq GenBank + S + T    Primary: CDS_0039, CDS_0040          gp17  R3-E3
accurate  phold.gbk       + S + T   Primary: + CDS_0052 (gp17)           gp17  R2-E3
```

**The evidence was in front of me.** The family list I was given was `D+G+S+T`, D
included. I saw S appear and credited the tier change to it without checking which family
R2 requires. The same slip is why item 32 concluded "not the vocabulary": gp17 is lifted
by the D/S disagreement over the identical words "tail fiber protein" — the vocabulary
divergence is not incidental to that result, it *is* that result.

Corrected in `CHANGES` items 32 and 33, `WORKFLOW.md` §2 and §5.1–5.2, both SKILL.md
files, `KNOWN_ISSUES.md` §11, `packaging/biomni/README.md` and the comparison plan. The
operative advice everywhere is now **configure a source of functional annotation**, not
"configure a database".

### 44. The vocabulary question has its first gold-standard data point (2026-11-21)

Item 36 stated a risk before the run rather than after. It resolved against `unified`:

```
mode             T7 accurate route                       gp17
current          Primary: 0039, 0040, 0052 (3)            R2-E3   in Primary
explicit_guard   Primary: 0039, 0040, 0052 (3)            R2-E3   in Primary
unified          Primary: 0039, 0040      (2)             R3-E3   OUT
```

On the literature gold standard `unified` gives the biologically wrong answer and the
default gives the right one. And the reason is the uncomfortable one: **the divergence
`unified` removes is what produces the correct answer.** Reconciling the two maps is
therefore not obviously a cleanup, and the burden of proof has moved onto `unified`.

The default has not moved and does not move on one strain. Two things changed in the plan:
the sweep must run the **accurate** route, because the D family `unified` flips exists
only where a PHROG annotation produced it — a fast-route sweep would show no gp17 effect
and answer the wrong question; and `explicit_guard` is now the more defensible candidate
for a future default than `unified`, being behaviour-identical to `current` here while
honouring `dual_role_precedence` on every reader.

### 45. Mode B lost family T for the whole genome, in silence (2026-11-21)

**The blocking defect from the test round.** `--phold <per_cds_predictions.tsv>` produced
`stage3_local.cds_searched: 0` with no warning anywhere, while a structure database was
configured — i.e. while the user had asked for that search. On the real *Salmonella*
genome it cost two true candidates: a 985 aa fibre subunit and an Ig-domain protein.

Root cause: phold's TSV has no translation column, so `phold.read()` produces records
without `translation`, and `stage3_local.prepare()` filtered on exactly that key. Two
different zeros were collapsed into one silent one.

They are now distinguished, which is the actual fix:

* **no CDS carries a sequence** — an input problem. Refused with exit 2, before any
  binary is resolved, and the message names all three ways out (`--genbank phold.gbk`,
  `--phold-genbank`, `--no-local-stage3`). The check sits before capability resolution on
  purpose: placed after, it would never be reached on a machine without foldseek.
* **no CDS is in the nominated pool** — a legitimate zero. Stays silent, because the
  empty pool is itself Stage 1's answer.

New `--phold-genbank` makes the TSV route usable rather than merely refused, which is
what the test round asked for: it reads translations from the GenBank phold wrote beside
the TSV, so the run keeps the TSV's per-CDS fields (`prostt5_confidence`, `qCov`, the
category proportions) that the GenBank does not carry. Matching is on coordinates with
the identifier as a cross-check, and **a match below 90% is refused** rather than
reported — a sequence attached to the wrong CDS is a confident wrong answer that nothing
downstream could detect. Mode A rejects the flag instead of ignoring it.

`tests/test_mode_b_sequences.py`, 11 tests. Four of them fail against the previous
commit, which is what makes them a regression test rather than a description.

### 46. `top_unresolved_candidates` is a conditional key (2026-11-21)

It appears only when the shortlist is empty or `completeness` is `limited`
(`cli.py:784-788`), so the top-level key count is **31 or 32** — my "31 top-level keys"
was a third wrong count of the same field. Documented in `WORKFLOW.md` §3 and the skill.

Making it always present with `null` would be the tidier contract and matches this
project's preference for explicit states over absence. Deliberately not done: a consumer
written `if "top_unresolved_candidates" in d: ...` would start reading a `None`, and test
rounds are in flight. It is a decision for the maintainer, not a silent change.

### 47. Documentation the test round earned (2026-11-21)

* **The route choice is bigger than "an hour of CPU".** On T7 it is the difference
  between the literature RBP being in the shortlist and not. The skill now shows both
  Primary sets so the user decides with that in front of them.
* **A new "when the user has a structure" section.** Measured against a cryo-EM map on
  the *Salmonella* phage: of 5 candidates, 4 structurally supported (including the top
  RBP, a 986 aa fibre with a Family 11 sugar-binding module), 1 relocated rather than
  wrong (a capsid decoration protein with Ig domains — not a tail RBP, but worth testing
  for sugar binding on its own), and 1 false positive (annotated "tail fiber protein";
  the map shows a tube initiator). Structural localisation outranks annotation transfer,
  and the agent should say where they disagree rather than defend the ranking.
* **The false positive's shape is worth naming:** it was pushed into Primary by a
  part-name annotation agreeing with itself across D and S (a 99.3%-identity homolog also
  called "tail fiber protein"). Agreement between two readers of the same part name is
  not two independent lines of evidence about function. De-correlation collapses tools,
  not vocabularies — which is known issue #4 from a new angle.
* **Nine troubleshooting entries, all measured**: the `foldseek databases PDB` prefix bug
  (a prefix with a directory component makes foldseek's own install script fail its `mv`
  and scatter the files, reported as `Cannot open lookup file ..._ca.lookup`); its
  three-argument form; `mmseqs.org` 302-ing to a repository page; Zenodo returning 403 to
  data-centre IPs while `pharokka install` calls it an MD5 mismatch; phold's pip-only
  install with `numpy<2` pinned; `-num_threads` not `-threads`; `/opt/conda` and apt
  packages not surviving hibernation; volumed RefSeq viral protein files.

## v1.2.3+linux

### 48. P0-2: presentation signals, with the guardrails that make them allowable (2026-11-21)

The test round found a false positive **no evidence family could have caught**: 01F2 gp16,
product "tail fiber protein", pushed into Primary by a 99.3%-identity homolog *also*
called "tail fiber protein". A cryo-EM map showed a tube initiator — baseplate assembly
machinery. Agreement between two readers of the same part name is not two independent
lines of evidence about function, but de-correlation collapses **tools, not vocabularies**,
so both records stood and nothing in the output said why to doubt them.

I had put the red-line question to the maintainer rather than guess, because both
requested signals need a new keyword list and a hint firing too often is noise while one
firing too rarely is dead weight. The answer reframed the line usefully: it was never
"do not touch the file" but *calibration-bearing values must not migrate silently* — and
whether something crosses it "depends on whether it can be shown not to affect a
conclusion", not on which file it lives in. Granted on four guardrails, all implemented:

1. **Inert, demonstrated.** `tests/test_presentation_signals.py` fills the whole
   vocabulary with terms matching everything and asserts the pool, families, tiers,
   ranking and Primary/Rescue are byte-identical. Measured under saturation: **16 of 16
   candidates firing, 196 hits on the letter `a` alone, zero conclusions moved.** A
   second test pins that the block is computed *after* ranking and after the sets are
   assigned, so the inertness is structural and not merely current behaviour. A third
   asserts the module mutates none of its inputs — a hint editing a record in place
   would reach the evidence chain through the back door, since records are written out.
2. **`calibration: pending`**, the same status semantics as the tiers.
3. **Trigger rate observable.** `term_trigger_counts` reports per-term hits.
4. **Recorded here**, and the T7/01F2 regression is the maintainer's to re-run.

Three checks, all read-only, all after the sets are fixed:

| | fires when |
|---|---|
| `structure_annotation_conflict` | the product name claims a fibre while a hit *description* describes baseplate/assembly machinery |
| `fibre_chaperone_adjacency` | a fibre assembly chaperone within 2 CDS downstream, **same strand** |
| `direction_conflict` | the candidate's own records disagree about direction |

Two details worth stating rather than burying:

* **The chaperone check follows the strand.** Fibre and chaperone are co-transcribed, so
  for a reverse-strand gene the chaperone is at *lower* coordinates. Reading "downstream"
  as "higher coordinate" would have silently missed every reverse-strand fibre; a test
  pins both orientations and that an opposite-strand neighbour is ignored as a different
  operon. The signal's own `action` text carries the maintainer's constraint — NOT a
  family, and the C1 positional evidence for the same CDS must not be counted twice.
* **The chaperone vocabulary is not new.** It points at
  `stage4.direction_map.supports_structural_only`, which already holds the helper nouns.
  Only the nine assembly-context words and four part names are new.

`direction_conflict` is the cheap variant the maintainer said to include if it came in
under 20 lines; it did, at 23 including the output structure. It is kept because it
catches something distinct — a T record fighting a D record, which known issue #4 says
the evidence model cannot express, since one representative per family cannot carry both
readings while `stage3.af3_priority` treats exactly that conflict as a P1 trigger. **A
test asserts it would NOT have caught gp16**, where D and S agreed and only the external
structure disagreed, so the limitation cannot be forgotten and it is never mistaken for a
substitute for the description check.

On the synthetic probe genome the default vocabulary fires **zero** times. That says it is
not trivially noisy and nothing more — the real rate needs the labelled strains, and
`complex` is the term most likely to be pruned, since PDB titles say it constantly. It is
in the list because the test round named it, not because it is known to discriminate.

### 49. The merge question resolved, and the count I keep getting wrong (2026-11-21)

The Biomni session diffed its own 1.2.2 package against `be5aaed` file by file and found
it a strict subset: nothing to merge back, and `/mnt/results` now carries the repository
build, so two 1.2.2 packages no longer coexist. `WORKFLOW.md`'s header said "Read from the
code at `2cb9ed1`" after the code had moved on; it now names the version rather than a
commit, so it cannot go stale the same way.

And `presentation_signals` moves the top-level key count again, to **32 or 33**. That is
the fourth value this one field has had in four commits (29, 31, 31-or-32, 32-or-33), and
three of my four statements of it were wrong. `WORKFLOW.md` now declines to quote it and
says to count it from a run — a number that changes every release is not documentation.

## v1.2.4+linux — calibrating the signals on real data

### 50. The signals' first real trigger counts, and what they changed (2026-11-21)

Both acceptance criteria from item 48 were met on 01F2 full-stack + T7: gp16 fired
`structure_annotation_conflict` on a T hit titled *"Baseplate Complex for Myoviridae Phage
XM1"* — exactly the tube-initiator identity the cryo-EM map showed — and gp23 fired
`fibre_chaperone_adjacency` (chaperone at offset +1, same strand) while gp16 did not.
Primary/Rescue were member-identical to `be5aaed`. `direction_conflict` fired on gp9 and
the CDP, the two candidates the deep read advanced on structural grounds, with D saying
structural-only and T saying receptor-binding — the conflict known issue #4 says the
family model cannot express.

Then the trigger counts did their job. `01F2: baseplate 1, complex 3, fiber assembly 4,
tail assembly 2`; T7 all zero. Three changes follow, all from the data:

**`complex` is pruned.** Of its 3 hits, 2 were *chaperone* proteins matching a Mu title
"Complex of Mu phage tail fiber and its chaperone" — a chaperone hitting a fibre-chaperone
complex structure is the normal case, not a conflict. The one true signal carries
`baseplate` as well, so nothing is lost. The word was in the list because the test round
named it; the data removed it. That is what `calibration: pending` plus
`term_trigger_counts` was for.

**Two looseness reports collapse into one rule, and the cause is a bug class this project
had already fixed twice.** A chaperone fired the conflict check for its own hit titles
because `"tail fiber assembly protein"` **contains the substring** `"tail fiber"`. That is
the same failure as the C1/C2 whole-token fix and as `normalize._direction`'s "a compound
name takes the conservative reading" — a rule the codebase already held, which I did not
carry over when writing a new matcher. The adjacency signal firing on a tail sheath is the
same shape: a chaperone next to a sheath does not make the sheath a fibre.

`self_product_names_another_part` now gates **both** checks: no signal for a CDS whose own
product positively names a different structural part. It points at
`stage4.direction_map.supports_structural_only` rather than restating it, so there is one
vocabulary to fix. `"tail fiber protein"` is not on that list and stays in scope, which a
test pins — otherwise the genuine case would be excluded with the chaperones.

**One deliberate difference from what was suggested.** The test round proposed ANDing
adjacency with the candidate's own fibre evidence. The gate implemented is narrower: skip
only what is positively named *something else*. Requiring positive fibre evidence would
also exclude a dark-matter candidate — a hypothetical protein with a chaperone immediately
downstream — which is the most interesting case this signal has. A test pins that such a
candidate still fires. Reversible if measured noise says otherwise.

`product_says=None` in the report was a key-name mismatch on the reading side: the field
is `product_claims`. A missing product correctly produces no signal, and both facts now
have tests so the contract is unambiguous either way.

Two of the item-48 tests failed after the pruning, because they asserted `complex` was in
the vocabulary — true when written, false now. They were updated rather than the code: the
tests caught a deliberate vocabulary change, which is what they are for.

### 51. A deployment trap that produced a false verification (2026-11-21)

The first verification round ran against the **old code**: a non-editable copy had been
installed into the venv and shadowed the editable path, so `import rbpfinder` resolved to
the copy and a `git checkout` changed nothing that ran. Reported and re-run clean.

Worth recording because it is the same class of error as an earlier one in this project —
running a venv's python from the source directory, which made `import rbpfinder` resolve
to the working tree and produced a false PASS. Both share a shape: **the thing that
executed was not the thing that was edited, and nothing in the output said so.**

Two troubleshooting entries added: verify with
`python -c "import rbpfinder,inspect;print(inspect.getsourcefile(rbpfinder))"` rather than
assume, and compare `versions.package` in `run_result.json` against the commit intended.
`uv pip install -e` survives a branch switch with no reinstall; changing *install method*
is what needs the check. Item 39's version recording is what makes the second check
possible at all.

### 52. The test-round reports are in the repository (2026-11-21)

`docs/test-rounds/` now holds the two Biomni reports, **verbatim** — their dates, their
stated commit and their rough edges unchanged, because a corrected evidence document is
no longer evidence. Checksums match the uploads. The provenance, the staleness and what
each predates are in a sibling README rather than edited into the files.

Linked from `WORKFLOW.md` §5 and the handoff's reading order, so the evidence sits next
to the claims it supports instead of being two orphan files.

The README also carries the **regression baselines** from the handoff's §4, which is the
actionable part of those documents: T7 full-stack `Primary = {CDS_0039, CDS_0040,
CDS_0052}`; 01F2 full-stack `Primary = {CDS_0010, CDS_0011, CDS_0012, CDS_0019}`,
`Rescue = {CDS_0026}` in pharokka numbering, with the prokka mapping; and the architecture
calls.

One of those baselines is worth reading twice. **T7 types as `E_jumbo_or_unresolved`,
which is wrong — T7 is a podovirus — and its Primary set is correct anyway.** That is
`architecture_is_soft_prior` demonstrated rather than asserted: the typing reaches
`k_default` and the priority loci and nothing else, so a wrong type cannot exclude a
candidate, and the `low` confidence is the typing reporting its own failure instead of
hiding it. It is the cleanest confirmation of that invariant the project has.

Two documents were offered and one was declined: an uploaded `WORKFLOW.md` turned out to
be byte-identical to this repository's own copy at `be5aaed`, two commits stale — missing
§4a, §5 item 15 and the version-based header. Committing it would have been a silent
regression dressed as a contribution, so it was checked against the git history before
anything was written. The second upload of the test report was byte-identical to the
first.

### 53. The bundle builder's docstring claimed a check it did not have (2026-11-21)

Noticed while confirming that `docs/test-rounds/` would not ship. The module docstring
said *"a file that is neither included nor forbidden is a new file nobody classified, so
the build stops and asks rather than guessing"*. It did not stop; `TREES`/`FILES` is an
allowlist, so an unlisted file is silently skipped.

That is my own overclaim, and the same shape as the `stage3_local` taxonomy comment in
item F4: a comment asserting a behaviour nobody had verified. The difference is what to
do about it. Here the claimed behaviour was worth having, so it was **implemented** rather
than the sentence softened.

`unclassified_packaging_paths()` reports anything under `packaging/` that is in neither
the allowlist nor the new `NOT_SHIPPED` map, and the build stops until it is classified.
The allowlist stays the thing that decides what ships — that direction is fail-safe, since
an unlisted file not shipping is better than one shipping by accident. This catches the
opposite failure: a new `packaging/` subtree that never reaches a user while no build
output mentions it.

Writing the `NOT_SHIPPED` reasons settled three boundaries that were previously just
absences: `packaging/comparison` (maintainer tooling — a user installs the analysis, not
the instrument used to decide what the analysis should do), `packaging/biomni/README.md`
and `packaging/biomni/setup_biomni.sh` (the Biomni route installs from a git checkout and
downloads multi-GB databases; shipping its setup script inside a bundle that
`install_linux.sh` already installs would offer two installers for one machine). A test
requires each entry to carry a reason, because a path excluded without one is
indistinguishable from an oversight.

A second test adds a stray file and asserts the check catches it. A gate that passes
everything is worse than no gate — the same reason the skill-package gate has one.

### 54. `unified` removes the false positive too, which reshapes the experiment (2026-11-21)

Computed, not run: on 01F2 gp16's measured family set — the cryo-EM-confirmed false
positive, annotated "tail fiber protein", actually a tube initiator — `unified` gives
`R3-E3` where `current` and `explicit_guard` give `R2-E3`. It **leaves Primary**.

So `unified` removes a false positive by exactly the mechanism that removed a true
positive on T7. Item 44 read as "`unified` is wrong on the gold standard"; with both cases
in hand it reads as a **precision/recall trade**, and `unified` is a different operating
point rather than a mistake. That is a materially different conclusion from the same
mechanism, and it would have been missed by testing only the recall side.

The more useful finding is what separates the two cases. gp17 and gp16 are
**indistinguishable to the direction vocabulary** — both "tail fiber protein", both with
part-name S homologs — which is why `unified` moves both and `current` moves neither. But:

```
                     structure_annotation_conflict           chaperone downstream
gp17  T7 true RBP    no   ("N-terminal of mature ... gp17")  --
gp16  01F2 false pos YES  ("Baseplate Complex for ... XM1")  no
gp23  01F2 true RBP  no                                      yes (+1, same strand)
```

**The presentation signal predicts which of the two `unified` should demote**, on
information a direction cannot carry: a direction is assigned from the product name, while
this reads the hit description. The signals built in item 48 to explain a false positive
turn out to separate a case the vocabulary structurally cannot.

`packaging/comparison/VOCABULARY-COMPARISON.md` now states the sharper experiment: across
the labelled set, does `structure_annotation_conflict` separate true from false part-name
candidates? If it does, the vocabulary answer is probably *neither arm* — keep `current` —
and the open question becomes whether this signal should ever graduate from presentation to
evidence, doing selectively what `unified` does indiscriminately. **Nothing here justifies
that**; it is a far larger change than a default flip and would need its own benchmark.
Two cases are two cases. The claim is only that the experiment now has a better shape.

### 55. The signal vocabulary can be calibrated without running any genomes (2026-11-21)

Seven of the eight assembly-context words have never fired on real data. The instinct is
to run more strains; that is the wrong instrument. Whether a word is noise depends on **how
common it is in structure hit titles**, not on which phage produced them — a word in a
large fraction of PDB titles fires on nearly every candidate regardless.

Every run already wrote `stage3_local/<cds>.foldseek.json` with its hit titles, so the
measurement costs no CPU and no new strains. `packaging/comparison/term_noise_floor.py`
counts, per term: titles containing it, CDS affected, how often it appears **alone**, and
which other vocabulary terms co-occur.

That last column is the one that matters, because it is the argument that pruned `complex`:
a term which never appears without a stronger one is redundant, and a term that fires alone
and often is noise. On a small realistic sample `wedge` and `hub` never appeared without
`baseplate` — if that holds on the real corpus they can go the same way, leaving a
vocabulary small enough to defend term by term.

### 56. Overfitting audit of the 01F2 round (2026-11-21)

Asked whether the corrections made after one genome are overfitted. They partly are, and
the risk is not spread evenly — so here is the audit rather than a reassurance.

**Not fitted** — these would stand if 01F2 had never been run:

* item 45, the Mode B defect. A format with no translation column producing a silent zero
  is a defect of the code, not of a genome. 01F2 only made it visible, and at a cost.
* item 43, the attribution correction. Its content is a mechanism statement — more
  families raise `tier_e`, only a direction raises `tier_r` — which follows from the
  tiering rules and would be true with no genome at all.
* item 50's `self_product_names_another_part`. It applies a rule the codebase already
  held ("a compound name takes the conservative reading") to a substring-matching bug that
  exists independently of any input.

**Fitted, and here is where** — `presentation_signals`' vocabulary:

```
baseplate           fired on real data, caught gp16            earned
wedge, hub          baseplate anatomy, plausible, unmeasured   never fired
tail terminator     "                                          never fired
assembly chaperone  "                                          never fired
tape measure        SIPHOVIRUS anatomy; 01F2 is a myovirus,    never fired
                    so it could not have fired on the genome
                    that produced this list
tube initiator      DERIVED FROM A HUMAN INTERPRETATION        never fired
initiator           OF A STRUCTURE, NOT FROM TEXT THE TOOL
                    READS -- justification VOID
```

Those last two are the sharpest instance and worth stating plainly. The cryo-EM map says
gp16 **is** a tube initiator. The matcher never sees that; it sees PDB titles, and gp16's
said "Baseplate Complex for Myoviridae Phage XM1". So the words were transcribed from the
*explanation* of the case rather than from anything in the data, which is overfitting to
an interpretation — a worse failure than overfitting to a measurement, because no amount
of the same data can falsify it. They are first to go once `term_noise_floor.py` has run,
and they are not removed on zero evidence only because the measurement is cheap and
pending.

**An architecture blind spot nobody had named.** The whole vocabulary is *myovirus* tail
anatomy, built from one *Salmonella* myovirus. T7 fired zero terms — which is
**uninformative, not reassuring**: a podovirus has no baseplate to name. Nothing has been
tested on a siphovirus. A zero on a non-myovirus therefore means "not applicable", never
"clean", and both skill packages and `WORKFLOW.md` §5 now say so, because an agent reading
an empty signal block as a clean bill of health is the way this overfitting would actually
cause harm.

**What bounds the damage.** The layer is inert and *tested* inert (item 48): a wrong
vocabulary costs attention, not correctness. No threshold moved, no default moved, no tier
rule changed. That is the whole reason it was acceptable to seed a vocabulary from one
case at all — and the protection ends the moment a signal is promoted to evidence, which
is why item 54 says nothing yet justifies that.

Also recorded: the adjacency window of 2 CDS is a guess around a single observation
(gp23's chaperone at +1), not a measured operon spacing. And the two documents in
`docs/test-rounds/` are one genome plus one gold standard, which is what the README's
provenance section is for.

## v1.2.5+linux — closing the round

### 57. Overfitting handled: two terms removed, five left falsifiable (2026-11-21)

Item 56 located the overfitting. This acts on it, and the treatment differs by **why** a
term was weak, because "unmeasured" and "no hypothesis at all" are different defects:

**Removed, on provenance rather than on counts** — `tube initiator`, `initiator`. The
cryo-EM map says gp16 *is* a tube initiator; the PDB title the matcher reads said
"Baseplate Complex for Myoviridae Phage XM1". These were transcribed from the human
*explanation* of the case, so there was nothing a larger run could falsify. Removal, not
measurement, is the remedy for that. The case is still caught — `baseplate` alone does
it, which a test now pins — so removal cost nothing. (`initiator` bare is also a generic
word that replication-initiator proteins carry, though the product-name gate bounds how
often that could matter.)

**Kept, deliberately** — `baseplate` (earned), plus `wedge`, `hub`, `tail terminator`,
`assembly chaperone`, `tape measure`. These are literature anatomy: unmeasured but
**falsifiable**, which is exactly what the two removed terms could not offer. Pruning them
now on zero data would discard a testable hypothesis; leaving them without an instrument
would make `calibration: pending` a status nobody could ever discharge.

So the instrument is now part of the normal path. `term_noise_floor.py --aggregate` rolls
up the `term_trigger_counts` every run already writes, so a large-scale application round
produces the calibration as a **by-product** — no extra step, no extra CPU. A term silent
across many genomes removes itself; a term firing on nearly all of them removes itself the
other way. Neither verdict needs a decision from anyone. Both skill packages now tell the
agent to report that rollup as a vocabulary finding.

Two tests failed on the removal, asserting the removed words were present — true when
written. Updated rather than the code, and one of them is now stronger: it asserts the
false positive is caught by `baseplate` **alone**, which is the evidence that the removal
was free.

### 58. The siphovirus gap, recorded as what it is (2026-11-21)

`A_sipho_gpJ` exists in the rules with its own markers, `k_default` and priority loci
(`gpJ central fibre`), and **nothing in this repository records it being exercised end to
end** — on the most common tailed-phage morphology. The maintainer reports siphoviruses in
the training set, so this is a gap in the *record* rather than certainly in the coverage,
and a large-scale round closes it without a purpose-built run.

Worth separating what is and is not at risk. A wrong architecture call is not: typing is a
soft prior, and T7 types as `E_jumbo_or_unresolved` — wrong, it is a podovirus — while its
Primary set stays correct. What is at risk is **recall being systematically low across a
whole morphology with nothing in any single run to reveal it**, because a run on one
genome cannot show that its class is being handled worse than another.

`WORKFLOW.md` §5 item 16 carries it. It is the clearest open question the scale round can
answer, and it needs no new strain selection, only that the morphologies be recorded
alongside the results.

### 59. Two defects in my own deployment path, found by deploying it (2026-11-21)

A real install from the merged `main` found both, and both were mine.

**`setup_biomni.sh` did a non-editable install** — `pip install "$REPO"` — walking straight
into the trap §2a of its own README warns about. Worse, the reuse guard only asked whether
the executable existed, so after a `git checkout` it printed *"already installed —
reusing"* and the new code was **never installed, silently**. That is the stale-code
failure mode built into the script that is supposed to prevent it, and item 51 had already
recorded it happening to a test round.

Now `-e`, and the guard asks **where the import resolves to** rather than whether a file
exists — the same question §2a asks a human, asked by the script about itself. Three
outcomes, each named in the output: reusing an editable install from the checkout,
reinstalling because the import came from somewhere else (with the shadowing explained), or
reinstalling because nothing was importable. Exercised both ways before committing.

**README §2 documented a deployment that does not register anything.** The `cp`s put files
where the platform looks; `check_skill.py` validates structure. **Neither registers the
skill**, so the deployment ended with the files correct, the gate passing, and nothing in
the skills list. Registration is a separate platform flow — the agent generates a preview
card and the user confirms the save — and §2 now says so, in the words to ask for.

And the sentence next to it was simply false. It claimed the frontmatter `id` meant
"deploying over the existing directory updates in place", and that changing the id would
register a duplicate. The platform assigns its **own** id on save; the id in the file is
whichever environment the package last came from, and is informational. I wrote that claim
from a prior session's description without verifying it, which is the same error as item
43's attribution and item 53's docstring: **asserting a mechanism I had not checked.** The
pattern is specific enough to name — it happens when a plausible explanation arrives from
somewhere trustworthy and I pass it on as established.

Also confirmed from the same round, worth recording as environment facts rather than
defects: the Foldseek CDN returns **429** (`error code: 1027`) to data-centre IPs with no
mirror, so `pdb100`/ProstT5 need a retry loop; and Zenodo did **not** block the PHROGs
download this time (656 MB, MD5 passed) — the 403 in item 47's troubleshooting is
intermittent, not permanent. The advice to back the database up to shared storage stands
for exactly that reason.

### 60. CORRECTION: family T is not corroboration (2026-11-21)

I told the maintainer that a Foldseek CDN block need not delay a large-scale round,
because T7 gp17 reaches `R2-E3` with or without family T. The arithmetic was right and the
conclusion was wrong: gp17 is a *well-annotated* CDS, where D and S already carry the
reading. Generalising from it is the same error as item 43 — reasoning from one case that
happens not to exercise the thing in question.

Computed on a dark-matter CDS — "hypothetical protein", so no D record, and no homolog
hit, so no S:

```
G only              R3-E1   can never enter Primary
G + functional T    R2-E2   enters
```

**T is load-bearing exactly where this tool is most needed.** It also gates audit A2,
which asks which CDS carry structural signal while their positional signal is negative —
a whole recall-recovery route, not a completeness nicety — and it is what
`structure_annotation_conflict` reads, so without T a part-name false positive is
invisible. `WORKFLOW.md` §5.5 said "T rarely lifts a true RBP alone", which is true of an
annotated CDS and misleading as a general claim; it now carries the dark-matter case
beside it, and both skill packages say a run without local Foldseek is weakest on the
genomes that need it most.

Also recorded: only `pdb100` and `cath50` are in `stage3.functional_databases`. The
AFDB-derived labels are capped at apparatus membership by
`stage3.apparatus_only_databases`, so substituting one of those adds a family and raises
`tier_e` without ever producing a receptor-binding direction — it cannot rescue the
dark-matter case. That narrows the acceptable substitutes to one: `cath50`.

`packaging/biomni/README.md` now has a ranked set of routes for the blocked download,
starting with a diagnostic that costs nothing: read the download hosts out of the Foldseek
binary already on the machine, since `foldseek databases` has historically served some
files from a university host rather than the CDN doing the rate-limiting. Then: check
whether phold's own ProstT5 weights are already present (phold runs ProstT5 + Foldseek, so
a working `phold install` fetched them from somewhere unblocked — though the formats may
differ and that must be verified, not assumed); try `cath50`; and failing those, transfer
from an unblocked IP, for which `RBP_BACKUP` already exists, with a HuggingFace dataset
repository as the medium most likely to be reachable from a sandbox that managed `phold
install`.

And what not to do, stated so it is not rediscovered: do **not** build the database by
hand. `foldseek createdb` succeeds, the result has no taxonomy files, and the search dies
at run time. `doctor` reports it `CONFIGURED, INVALID` on purpose — item F4's lesson.

## v1.2.8+linux — three defects a real deployment found

### 61. A run could report a version two releases stale (2026-11-22)

**The hole in item 39, and it was in exactly the setup I recommend.** `versions.package`
comes from distribution metadata, frozen when `pip install` ran; `versions.rules` is read
live from the YAML. On an **editable** install `git checkout` moves the files and leaves
the metadata behind, so a run reports:

```
{"package": "1.2.5+linux", "rules": "1.2.7+linux"}      <- and was believed
```

Reproduced here before fixing: install at one commit, change the files, run — two releases
apart, no complaint. Item 39 existed so that a benchmark number could be attributed to a
release; in the editable setup both this project's docs and the Biomni deployment use, the
number silently goes stale on every checkout. Recording a version is worthless if the
recorded value can be wrong without saying so.

The two fields were already side by side, so the check is free. `versions.consistency` is
now `ok`, `package_metadata_stale` (with the remedy in `note`) or `not_installed` —
`0+unknown` being a different state, no release to attribute to rather than the wrong one
— and each non-ok case appends a **run flag**, because a field buried in JSON is not
something anyone reads. The run still succeeds: the code that executed *is* the
checked-out code, so the analysis is right and only the reported number was stale.

Four tests, including that a consistent install is **not** flagged — a check that fires
when it should not becomes noise people filter out.

### 62. A test was testing the machine, not the code (2026-11-22)

`test_an_empty_nominated_pool_is_not_an_error` asserted that resolving Foldseek raises
`CapabilityUnavailable`, relying on the ambient machine not having Foldseek installed. It
failed on the Biomni platform, where `/workspace/bin` is always on PATH by design.

Reported — correctly — as not a code defect. It is a **test** defect, and mine: a test
whose result depends on what happens to be installed proves nothing either way. It now
empties `PATH` and clears `RBPFINDER_FOLDSEEK` with `monkeypatch`, so the binary is
genuinely unresolvable by construction. The suite was then run **both** with and without
Foldseek on PATH — 244 passing in each — because fixing this by making it pass here would
have repeated the original mistake in the other direction.

### 63. The backup used `cp -a` on a mount that cannot take it (2026-11-22)

`setup_biomni.sh` backed the structure databases up with `cp -a`, which failed on a shared
mount with no symlink or `utime` support. That was the root cause of a backup that would
not restore, found by someone actually relying on it — I had not hit it because my storage
behaves differently, which is the whole value of someone else running the thing.

A Foldseek database contains symlinks, so the copy cannot be flattened either. The backup
is now a **single tar file** on the shared mount, which sidesteps the mount's limitations
entirely: it only has to hold one regular file, and tar reconstructs the layout on local
disk. Verified round-trip, symlink preserved. Written to `.partial` and moved into place,
so an interrupted backup cannot leave a truncated archive a later restore would trust;
directory backups from the older script are still restored, with the symlink caveat
printed if it fails.

One thing recorded that I did not know and would have got wrong: a Foldseek database
records its own paths, so restoring to a **different** directory needs
`foldseek mvdb <old> <new>` rather than `mv`. This script restores to the path it backed
up from and so does not need it — but a relocating restore script does, and the deployment
had already discovered that independently and built one, verified against the online copy
(41 files, identical search results). That script is better evidence than anything I could
reconstruct from a description; it belongs in `packaging/biomni/` if its author will share
it.

## v1.2.9+linux — the structure route, made usable and attributable

### 64. The structure handoff could not be acted on (2026-11-25)

`AF3_MONOMER_BATCH.tsv` carried the candidate id, the trigger, a checksum and the reason
— **but not the sequence**. Nobody handed that file could submit anything, and
`stage3.af3_priority.P1_mandatory` calls structure "the arbiter" for six triggers.
`HHPRED_BATCH` had carried the sequence all along. The deferral was recorded in a comment
citing two scripts that read the file by column name; neither exists in this repository,
and adding a column is backward compatible for a name-based reader regardless.

The provider metadata also hardcoded AlphaFold Server, obscuring that the contract is
**predictor-agnostic**: `return_via: foldseek` means what returns is a structure searched
with Foldseek, as `foldseek_json` — the same kind local Stage 3 writes. Anything producing
a PDB/mmCIF satisfies the step. And nothing had ever stated the obvious cost: this build
refuses every remote submission with exit 2 and promises sequences never leave the
machine, while the documented handoff pointed at a hosted service. The tool is not the one
transmitting; the user is, and they chose this build for that promise.

### 65. Which backend earned each family (2026-11-25)

`independent_families` said a family counted, never what produced it. With two routes to
family T that is the difference between an attributable number and an unattributable one.
Candidates now carry `family_provenance` — the representative's tool per counted family —
and `structure_query`.

Found while doing it: the literal I matched covered two early-return branches and the
geometry record but **not the main record**, which is the one that counts and the one
carrying `probability_pct`. Caught by reading the output, not the test result. And the
geometry record takes no `hits` argument, so the inference landed there as a latent
`NameError` — the suite stayed green because nothing calls it, which is this project's
standing argument about dead code made concrete.

### 66. The route was being guessed, and guessed wrong (2026-11-25)

A real structure-structure run on the Biomni side found it. A probability can be absent
for two unrelated reasons — no backbone coordinates (ProstT5), or an output format that
did not request the column — and `query_kind` read the second as the first. **The field
added to prevent mis-attribution was mis-attributing.**

Now: a declaration beats inference, inference promotes to `backbone_structure` only when
a probability is genuinely present, and anything else is **`unknown`** rather than a
guess. Local Stage 3 stamps its own artefacts `sequence_3di`, since it ran ProstT5 and
knows — without which the commonest route would report `unknown` forever, ProstT5 output
being exactly the shape inference cannot resolve.

### 67. A converter for the HPC route, not just a warning about it (2026-11-25)

Measured in the same round: of Foldseek's native CLI column names only `prob` and
`target` coincide with the web-JSON names `parse.foldseek.read` expects. Wrap CLI rows as
JSON directly and `evalue`, `bits`, `fident`, `qstart`, `taxname` all land as **None,
silently** — the hits survive while everything qualifying them vanishes. Worse, with the
probabilities gone the route was then inferred as ProstT5.

`local_foldseek.cli_rows_to_document` is now public: it maps the columns, refuses rows
from more than one query (a structure attributed to the wrong protein is undetectable
downstream), and stamps the route. Verified against the real CLI row from the test round
— every field lands. The symptom signature, **probability present and everything else
null**, is pinned as a test so it is recognisable rather than merely described.

While exposing it I split `LocalFoldseek` in half with a stray mixin, leaving it with only
`__init__`. The file parsed, so a syntax check said nothing. Reverted and redone with the
function after the class, structure verified by walking the AST rather than reading the
diff — the same shape of failure as the trap above: surface check passes, substance
broken.

### 68. Three executor tiers, decided rather than drifted into (2026-11-25)

Until now the rules knew only "the tool never transmits", which left no way to describe a
destination the **user** trusts but the tool does not run on. The maintainer's decision:
public web services stay refused with exit 2 everywhere; the user's own machine is the
batch handoff; a **session-trusted HPC cluster is allowed** — never by default, never
silently, confirmed every run, and recorded in the report so "the destination was trusted"
stays checkable afterwards instead of being a claim made in the moment.

**Scoped to candidates, not the genome.** Genome-wide folding is hours of cluster time for
a question this route does not ask, and it would not lift the A2 ceiling: measured here
with imported structural evidence for **12 of 12 CDS**, A2 still reported
`not_executable_due_to_missing_genomewide_T_or_M`, because the probe reads the local
search's provenance and never the imported records. The rules carry that measurement so
the proposal does not return ungrounded.

Both skills also now state what a second structural route most invites getting wrong: a
ProstT5 hit and a predicted-structure hit **collapse to the same T**. De-correlation counts
families, never tools, so the second route adds confidence in a reading and not a vote.
And pLDDT describes the predictor's confidence in its own model, not whether the protein
binds a receptor.

## v1.2.10+linux — a review of the deployed package

### 69. KNOW_HOW kept the attribution SKILL.md had corrected (2026-11-25)

Item 43 corrected the gp17 attribution in nine places. It missed the tenth, and the tenth
was the worst one: `references/KNOW_HOW.md` still read *"what lifts a candidate is the
**S family** — with T corroborating"* for four commits after SKILL.md said the opposite.

KNOW_HOW is the agent's **reasoning companion**. Whichever document it reads first is the
model it believes, and the "where the two differ, SKILL.md wins" tiebreak does not help
when nothing signals that they differ. Two guidance paths handing out opposite models of
what moves a tier is worse than either being wrong alone.

It now carries the measured mechanism — more families raise `tier_e`, only a direction
raises `tier_r`; a full RefSeq viral database left gp17 at `R3` because its top-10
homologs are all part names; what moved it was a D family from a PHROG annotation — and
the dark-matter case, where T is the only family that can speak at all. A test asserts
the superseded wording cannot return.

Reported by the Biomni session reviewing the deployed package, which is the only way this
was going to be found: I had grepped for the claim and fixed what I saw, and this
phrasing was different enough to survive it.

### 70. Three smaller corrections from the same review (2026-11-25)

**phold on bioconda.** The troubleshooting table asserted it is not there, from the
2026-11-19 round. The 2026-11-25 round installed it with `mamba -c bioconda phold`,
exit 0. Rather than flipping the claim, both rounds are now on record with the advice to
try conda first and fall back — a transient failure and a permanent absence look identical
from one attempt, and two contradicting reports is exactly when a document should stop
asserting.

**The direction table read as though one vocabulary decided.** It lists
`"tail fiber protein"` under apparatus, while the D vocabulary reads the same words as
receptor binding. Both are correct; the divergence is deliberate and measured
(2026-08-19), and on T7 it is what lifts gp17. The table now says so, because read alone
it invites someone to "fix" the inconsistency.

**The top-level key count is gone from the documents.** Stated wrongly four times — 29,
31, 31-or-32, then 34-or-35 while bumping this very release, each time from memory on a
line that already said to count it from a run. Removed rather than corrected a fifth
time; the conditional description was always the part that mattered. A test now fails if
any skill document quotes one.

## Known limitations carried forward

- **Window merging still happens in rotated linear space.** If two windows *both* extend
  across the cut they overlap on the circle without merging. That needs the largest
  anchor-free gap to be smaller than the two extensions reaching into it — rare, unlike
  the origin-splitting defect this replaced.
- **`tail fiber` is still judged oppositely by two vocabularies in the default mode**:
  `stage4.direction_map` reads it as receptor-binding on a CDS's own product name, while
  `stage2.annotation_term_map` reads it as `apparatus` on a homolog or structure hit. Since
  that is the most common real-world RBP description, homolog evidence systematically fails
  to produce a receptor-binding family. `rbp_ontology` now reaches the product-name reader
  under `--vocabulary unified` (items 33-36), but the default is unchanged and stays
  unchanged until the 42-strain comparison says otherwise.
- Bare `receptor` and bare `lectin` are absent from `stage2.annotation_term_map`, so
  "receptor recognition protein" and "C-type lectin domain-containing protein" read as
  uninformative. Same dependency.
- **`unified` has no helper-noun vocabulary.** `rbp_ontology` holds no "assembly
  protein" / "chaperone" terms, so under `unified` a chaperone reads `apparatus` instead
  of `structural_only`. Both are non-binding and neither is promoted, but the role label
  degrades to `unresolved`. Adding helper terms is a further vocabulary change with its
  own measurement.
- **`--offline` dependency wheels are platform-specific.** `pip download` fetches
  wheels for the interpreter and OS doing the build, so a bundle built on cp313/linux
  cannot install offline on Windows or another Python minor version. `VERSION.json`
  records exactly what it got (`offline_wheels_built_for`) rather than implying universal
  coverage; a Windows offline bundle has to be built on Windows.
- **Dead remote code is still in the tree.** `stage2_remote`, `interpro_remote` and
  `_remote_consent` remain, with `interpro_remote` having no caller at all and
  `--allow-remote-annotation` not even reaching argparse. Unreachable rather than
  dangerous, and the docs now say that finding it is not evidence of a remote route.
  Removing it is a separate change.
- **The licence is unresolved and now baked into a built artefact.** `LICENSE` says
  `All rights reserved` and the wheel metadata says `Proprietary`. That is the repository's
  current stated intent, so the bundle carries it; changing it later means re-cutting any
  bundle already distributed.
- **The presentation signals' vocabulary has one genome's calibration** (items 48, 50).
  `complex` is pruned and the self-product gate is in; the remaining eight
  assembly-context words have been measured on 01F2 and T7 only, where `baseplate` was
  the sole one to fire on a true signal. `wedge`, `tube initiator`, `initiator`, `tail
  terminator`, `assembly chaperone`, `hub` and `tape measure` have not fired on real data
  yet — unmeasured rather than shown useful. Being inert, a wrong vocabulary here costs
  attention rather than correctness.
- **pharokka upstream, not this repository**: version 1.10.1's `input_commands.py` has an
  MMseqs2 version allowlist that omits v19 and raises `UnboundLocalError`; `pharokka
  install` reports a Zenodo 403 block page as an MD5 mismatch. Both worth an issue at
  `gbouras13/pharokka`.
- Upstream items left alone: `transports.py` and `KNOWN_ISSUES.md` reference other
  `scripts/` files that still do not exist (`benchmark_stage2_48.py`,
  `m7c_pipeline_metrics.py`, `fetch_uniprot.ps1`, `pre_e4_localisation.py`); `packaging/install/*` expecting a `runtime/*.whl` that only exists
  in a release bundle; internal milestone logs and `standing_authorisation` in the rules
  file; `KNOWN_ISSUES.md` being the only Chinese document.
