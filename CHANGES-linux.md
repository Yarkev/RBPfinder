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

Not yet validated against a real foldseek binary — see `KNOWN_ISSUES.md` §10.

### 7. `rbpfinder validate-input`

Answers the precondition question in seconds, using the same readers the run uses, so an
orchestrator can check an annotation **before** spending an hour on phold or on a run
that cannot work. `--json` for machine consumption.

### 8. Biomni integration (`packaging/biomni/`)

Three `add_tool`-compatible tools — plan the route with the user (fast vs accurate,
default fast), check the annotation, run the analysis — plus a know-how document and an
idempotent `/workspace` setup script. Biomni annotates; RBPfinder classifies.

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

## Known limitations carried forward

- **Window merging still happens in rotated linear space.** If two windows *both* extend
  across the cut they overlap on the circle without merging. That needs the largest
  anchor-free gap to be smaller than the two extensions reaching into it — rare, unlike
  the origin-splitting defect this replaced.
- **`tail fiber` is judged oppositely by two vocabularies**: `stage4.direction_map` reads
  it as receptor-binding on a CDS's own product name, while `stage2.annotation_term_map`
  reads it as `apparatus` on a homolog or structure hit. Since that is the most common
  real-world RBP description, homolog evidence systematically fails to produce a
  receptor-binding family. `rbp_ontology` holds the reconciled vocabulary but is still
  shadow: adopting it moves tiers and needs the benchmark.
- Bare `receptor` and bare `lectin` are absent from `stage2.annotation_term_map`, so
  "receptor recognition protein" and "C-type lectin domain-containing protein" read as
  uninformative. Same dependency.
- Upstream items left alone: the missing `scripts/` referenced by `transports.py` and
  `KNOWN_ISSUES.md`; `packaging/install/*` expecting a `runtime/*.whl` that only exists
  in a release bundle; internal milestone logs and `standing_authorisation` in the rules
  file; `KNOWN_ISSUES.md` being the only Chinese document.
