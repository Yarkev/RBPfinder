"""M20-b1 -- what an InterPro match means, decided before any transport exists.

M20-a enriched: UniProt describing the proteins Stage 2 had already found, attached to the
S record that produced them, adding no family and moving no tier. This is a different kind
of thing. A signature matched on the candidate's OWN sequence is a second, independent
observation, so it reaches `independent_families`, `direction`, `tier`, Primary/Rescue and
Completeness. That is a scientific change, and it is frozen here, offline, before there is
a provider that could produce one.

Two rules carry the weight.

**An identifier is not an observation.** UniProt hands out InterPro accessions for free on
every entry it returns, and M20-a already carries them as associations. If one of those
were read as D, a single BLAST hit would become two independent families -- once as
similarity, once as the subject's own annotation. Eligibility is therefore checked twice,
on two unrelated grounds: the declared `origin`, and whether the match carries a span on
OUR candidate. A cross-reference cannot have the second, because nothing was ever aligned
to our candidate to produce it.

**A property is not a function.** Coiled coil, transmembrane helix, signal peptide,
disordered region: these are what a sequence is like, not what it does. Tail fibres are
full of coiled coil and so are hundreds of proteins that bind nothing. They are recorded,
and they carry no direction in either sense.

Nothing here decides science on its own. The categories, their ranking, the direction map
and the selection policy all live in `interpro_evidence` in the rules file; this module
applies them.
"""
from . import terms

_BASE = "interpro_evidence"

ORIGIN_EXECUTION = "interpro_execution"
ORIGIN_XREF = "uniprot_cross_reference"

# The parser in `parse/interproscan.py` predates this contract and uses its own names.
# Mapped rather than renamed: that file is a parser, and changing what it emits would
# quietly change what the existing local-import path produces.
_PARSER_ALIASES = {
    "accession": "signature_accession",
    "name": "signature_name",
    "type": "signature_type",
    "library": "database",
    "entry_accession": "interpro_accession",
    "entry_description": "interpro_description",
}

# `terms.classify` speaks its own vocabulary. It has no `non_rbp_identity` output, which
# is why `categories_reachable_without_curation` records that the category exists but
# cannot be reached until the curated layer is populated.
_TERMS_TO_CATEGORY = {
    terms.POSITIVE: "receptor_binding",
    terms.APPARATUS: "apparatus",
    terms.STRUCTURAL: "structural_only",
    terms.NOISE: "uninformative",
    terms.UNINFORMATIVE: "uninformative",
}


class ContractViolation(Exception):
    """A match that the contract cannot describe. Never a statement about the protein."""


def normalize(row, candidate_id, origin):
    """One raw match -> the frozen normalized match.

    Accepts both this contract's field names and the existing parser's. Absent fields
    become None; nothing is inferred, and in particular a missing span stays missing
    rather than defaulting to the whole protein -- the span is what makes the match
    eligible, so inventing one would defeat the check that depends on it.
    """
    if origin not in (ORIGIN_EXECUTION, ORIGIN_XREF):
        raise ContractViolation(
            "unknown origin %r; a match whose provenance is unknown cannot be judged"
            % (origin,))
    src = dict(row or {})
    for old, new in _PARSER_ALIASES.items():
        if old in src and new not in src:
            src[new] = src[old]
    out = {"candidate_id": candidate_id, "origin": origin}
    for field in ("signature_accession", "signature_name", "signature_type", "database",
                  "interpro_accession", "interpro_description", "start_aa", "end_aa",
                  "evalue", "score"):
        out[field] = src.get(field)
    return out


def eligibility(match, rules, length_aa=None):
    """May this match become D evidence? Returns (bool, reason).

    The reason is kept whether it passes or not, so a rejected match can be shown to a
    reader rather than disappearing.
    """
    want = rules.get(_BASE + ".d_eligibility.require_origin")
    if match.get("origin") != want:
        return False, ("origin is %s, not %s -- an identifier copied from a subject's "
                       "UniProt entry describes that subject, not this candidate"
                       % (match.get("origin"), want))
    if not rules.get(_BASE + ".d_eligibility.require_query_span"):
        return True, "span not required by the rules"
    start, end = match.get("start_aa"), match.get("end_aa")
    if start is None or end is None:
        return False, ("no span on the candidate; nothing was aligned to this sequence "
                       "to produce one")
    try:
        start, end = int(start), int(end)
    except (TypeError, ValueError):
        return False, "span is not numeric (%r..%r)" % (match.get("start_aa"),
                                                        match.get("end_aa"))
    if start < 1 or end < start:
        return False, "span %d..%d is not a range on this candidate" % (start, end)
    if length_aa and end > int(length_aa):
        return False, ("span %d..%d runs past the candidate's %d residues"
                       % (start, end, int(length_aa)))
    return True, "scanned on this candidate, span %d..%d" % (start, end)


def category_of(match, rules):
    """Return (category, basis). `basis` says which layer decided, for the record."""
    onto = _BASE + ".direction_ontology"
    curated_ipr = rules.get(onto + ".by_interpro_accession") or {}
    curated_sig = rules.get(onto + ".by_signature_accession") or {}

    ipr = match.get("interpro_accession")
    if ipr and ipr in curated_ipr:
        return curated_ipr[ipr], "curated_interpro_accession"
    sig = match.get("signature_accession")
    if sig and sig in curated_sig:
        return curated_sig[sig], "curated_signature_accession"

    # Checked before the description, and deliberately. A signature that reports a
    # sequence property has no functional reading however its description is worded.
    db = (match.get("database") or "").upper()
    if db in {str(x).upper() for x in rules.get(onto + ".non_directional_databases")}:
        return "non_directional", "non_directional_database"
    if match.get("signature_type") in set(rules.get(onto + ".non_directional_types")):
        return "non_directional", "non_directional_type"

    text = " ".join(filter(None, [match.get("interpro_description"),
                                  match.get("signature_name")]))
    cat, matched = terms.classify(text, rules)
    if cat == terms.UNINFORMATIVE and not matched:
        return rules.get(onto + "._default"), "default"
    return _TERMS_TO_CATEGORY[cat], "description_terms:" + ",".join(matched)


def _strength(category, basis, match, rules):
    base = _BASE + ".strength"
    if category == "non_directional":
        return rules.get(base + ".non_directional")
    if basis.startswith("curated_"):
        return rules.get(base + ".curated_accession")
    # A description reading is capped below `strong` however good the e-value: the
    # e-value says the signature matched, not that we read its description correctly.
    return rules.get(base + (".description_terms_with_evalue"
                             if match.get("evalue") is not None
                             else ".description_terms_without_evalue"))


def _region(match, rec):
    return {"start_aa": int(match["start_aa"]), "end_aa": int(match["end_aa"]),
            "source": "interpro_domain_span"}


def _full_region(rec):
    return {"start_aa": 1, "end_aa": max(rec.get("length_aa") or 1, 1),
            "source": "manual"}


def interpret(rec, matches, rules, provider_id, searched=True):
    """Normalized matches for ONE candidate -> one EvidenceRecord, family D.

    Three outcomes are kept apart, the same three that `sequence_homology.build` had to
    be fixed to distinguish: nobody scanned this; a scan ran and found nothing; a scan
    ran, found things, and not one of them was eligible to count.
    """
    rank = rules.get(_BASE + ".category_rank")
    to_dir = rules.get(_BASE + ".category_to_direction")

    if not searched:
        return {"family": "D", "tool": provider_id, "region": _full_region(rec),
                "direction": "uninformative", "strength": "weak", "not_run": True,
                "raw": {"hit_description": "no InterPro scan was run for this candidate"}}

    eligible, rejected = [], []
    for m in matches or []:
        ok, reason = eligibility(m, rules, rec.get("length_aa"))
        (eligible if ok else rejected).append((m, reason))

    if not matches:
        return {"family": "D", "tool": provider_id, "region": _full_region(rec),
                "direction": rules.get(_BASE + ".zero_matches.direction"),
                "strength": "weak", "not_run": False,
                "raw": {"hit_description": "InterPro returned zero matches "
                                           "(dark matter, not counter-evidence)",
                        "raw_match_count": 0, "eligible_match_count": 0,
                        "rejected_matches": []}}

    if not eligible:
        # The third state. Identifiers were present and every one of them was refused;
        # saying "zero matches" here would misreport what happened, and saying `not_run`
        # would misreport it in the opposite direction.
        return {"family": "D", "tool": provider_id, "region": _full_region(rec),
                "direction": "uninformative", "strength": "weak", "not_run": False,
                "raw": {"hit_description": "matches were present; none was eligible to "
                                           "count as domain evidence",
                        "raw_match_count": len(matches), "eligible_match_count": 0,
                        "rejected_matches": [{"signature_accession":
                                              m.get("signature_accession"),
                                              "interpro_accession":
                                              m.get("interpro_accession"),
                                              "origin": m.get("origin"),
                                              "reason": why}
                                             for m, why in rejected]}}

    scored = []
    for m, _why in eligible:
        cat, basis = category_of(m, rules)
        scored.append((rank.get(cat, 99), cat, basis, m))
    scored.sort(key=lambda t: (t[0],
                               t[3].get("evalue") if t[3].get("evalue") is not None
                               else 1e9,
                               str(t[3].get("signature_accession"))))
    _r, cat, basis, best = scored[0]

    return {
        "family": "D", "tool": provider_id, "region": _region(best, rec),
        "direction": to_dir[cat], "strength": _strength(cat, basis, best, rules),
        "not_run": False,
        "raw": {"hit_id": best.get("interpro_accession")
                          or best.get("signature_accession"),
                "hit_description": (best.get("interpro_description")
                                    or best.get("signature_name") or ""),
                "category": cat, "category_basis": basis,
                "database": best.get("database"),
                "evalue": best.get("evalue"),
                "raw_match_count": len(matches),
                "eligible_match_count": len(eligible),
                # Co-occurrence is preserved rather than collapsed: a depolymerase that
                # is both a hydrolase and a receptor-binding protein should be visible
                # as both to whoever reads the record.
                "all_categories": sorted({c for _r2, c, _b, _m in scored}),
                "rejected_matches": [{"signature_accession":
                                      m.get("signature_accession"),
                                      "interpro_accession": m.get("interpro_accession"),
                                      "origin": m.get("origin"), "reason": why}
                                     for m, why in rejected]},
    }


def select_candidates(candidates, rules):
    """Which candidates are worth an InterPro scan, in order. Returns (selected, skipped).

    Ordered by what the evidence still needs, then truncated -- not filtered by doubt,
    and never the whole pool. `af3_priority.forbidden_rule` names why a doubt-based rule
    produces selection bias; the same argument applies here, with the added cost that
    every extra candidate is unpublished sequence leaving the machine.
    """
    base = _BASE + ".candidate_selection"
    order = rules.get(base + ".order")
    excludes = rules.get(base + ".exclude")
    cap = rules.get(base + ".max_candidates")

    def families(c):
        return set(c.get("independent_families") or [])

    def directions(c):
        return {e.get("direction")
                for region in c.get("regions") or []
                for e in region.get("evidence") or []}

    def excluded(c):
        if "D" in families(c):
            return "already_has_d"
        if c.get("tier_r") == "N":
            return "not_a_candidate"
        if not c.get("translation"):
            return "no_sequence"
        return None

    def bucket(c):
        if c.get("tier_r") == "R2":
            return "r2_unresolved"
        if (c.get("tier_r") == "R3"
                and "supports_receptor_binding" in directions(c)):
            return "r3_with_receptor_binding_hint"
        if c.get("af3_priority") == "P1":
            return "high_priority_unresolved"
        if c.get("reopened_by_stage6"):
            return "stage6_reopened"
        return None

    rank = {o["id"]: i for i, o in enumerate(order)}
    picked, skipped = [], []
    for c in candidates or []:
        why_not = excluded(c)
        if why_not:
            skipped.append({"cds_id": c.get("cds_id"), "reason": why_not})
            continue
        b = bucket(c)
        if b is None:
            skipped.append({"cds_id": c.get("cds_id"), "reason": "no_selection_rule_matched"})
            continue
        picked.append({"cds_id": c.get("cds_id"), "reason": b, "rank": rank[b]})

    picked.sort(key=lambda p: (p["rank"], str(p["cds_id"])))
    if len(picked) > cap:
        for p in picked[cap:]:
            skipped.append({"cds_id": p["cds_id"],
                            "reason": "over_max_candidates(%d)" % cap})
        picked = picked[:cap]
    return picked, skipped


def independence_note(d_record, s_record):
    """What a reader must not conclude from a D record sitting beside an S record.

    Kept as data rather than prose so the report and the tests read the same statement.
    """
    enr = ((s_record or {}).get("raw") or {}).get("uniprot_enrichment") or {}
    xref_iprs = sorted({x["id"] for e in enr.get("entries") or []
                        for x in e.get("cross_references") or []
                        if x.get("database") == "InterPro"})
    return {
        "d_family_counted": bool(d_record) and not d_record.get("not_run"),
        "uniprot_interpro_xrefs": xref_iprs,
        "xrefs_counted_as_d": False,
        "note": ("UniProt listed %d InterPro identifier(s) for the subjects this "
                 "candidate resembles. None of them is domain evidence about this "
                 "candidate." % len(xref_iprs)) if xref_iprs else
                "no InterPro identifiers were carried from UniProt for this candidate",
    }
