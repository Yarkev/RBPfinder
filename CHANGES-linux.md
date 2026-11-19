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
Primary. The cause was evidence starvation, as diagnosed, not the vocabulary.

---

### 33. The three vocabularies, measured before anything was changed (2026-11-18)

Item 32 established that the gp17 failure was evidence starvation, not vocabulary. That
removed the urgency but not the divergence, so it was measured rather than assumed. Three
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
- **Dead remote code is still in the tree.** `stage2_remote`, `interpro_remote` and
  `_remote_consent` remain, with `interpro_remote` having no caller at all. The flags that
  reached them are refused before any ingest, so this is unreachable rather than dangerous
  — but finding it is not evidence that a remote route exists, and the docs now say so.
  Removing it is a separate change.
- Upstream items left alone: the missing `scripts/` referenced by `transports.py` and
  `KNOWN_ISSUES.md`; `packaging/install/*` expecting a `runtime/*.whl` that only exists
  in a release bundle; internal milestone logs and `standing_authorisation` in the rules
  file; `KNOWN_ISSUES.md` being the only Chinese document.
