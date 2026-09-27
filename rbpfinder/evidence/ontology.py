"""M20-b2 -- the reconciled vocabulary. SHADOW: nothing in the live path reads this yet.

`terms.py` applies `stage2.annotation_term_map` and is unchanged. This applies
`rbp_ontology`, which merges that map with `stage4.direction_map` and resolves the five
terms where they disagreed. Both exist side by side so the difference can be measured
before anything is switched -- the M14D product numbers are frozen, and a vocabulary
change moves tiers.

Two things it does that `terms.classify` cannot.

**A term may hold more than one role.** A tailspike is part of the tail apparatus AND the
protein that binds the receptor. The old map had to pick, picked `apparatus`, and would
have demoted a confirmed RBP core to "somewhere in the tail" had the P22 control not also
carried a depolymerase fold. `direction` still answers one question -- does this support a
direct RBP reading -- but `roles` no longer has to lie to let it.

**Interpretation confidence is separate from match confidence.** An e-value says a
signature matched. It says nothing about whether that signature's biology means receptor
binding. Keeping them apart is what stops a fold-level hit with a spectacular e-value from
becoming strong receptor-binding evidence.
"""
from . import terms

_BASE = "rbp_ontology"

_RANK = {"strong": 0, "moderate": 1, "weak": 2}
_DIRECTION_RANK = {"supports_receptor_binding": 0, "supports_structural_only": 1,
                   "supports_apparatus_membership": 2,
                   "supports_non_rbp_identity": 3, "uninformative": 4}


def _index(rules):
    return rules.get(_BASE + ".terms")


def classify(text, rules):
    """Return the reading for one annotation string.

    {matched, roles, direction, interpretation_confidence}

    Longest match wins among terms of the same direction, so `receptor binding` is not
    outranked by an incidental `binding`; direction rank breaks ties across categories,
    the same conservative ordering `terms.RANK` already uses.
    """
    low = (text or "").lower()
    # The same token rule the live reader uses. Without this the shadow ontology keeps
    # the defect M20-b2C2 removed from the live path -- `portal` would still match `Tal`
    # inside `por-TAL` here, and every future migration measurement would be comparing a
    # fixed reader against a broken one.
    token_terms = {str(t).lower()
                   for t in rules.get("stage2.annotation_term_map.token_match_terms")}
    hits = []
    for t in _index(rules):
        term = str(t["term"])
        if term.lower() in token_terms:
            if terms._token_pattern(term).search(text or ""):
                hits.append(t)
        elif term.lower() in low:
            hits.append(t)
    if not hits:
        return {"matched": [], "roles": [], "direction": "uninformative",
                "interpretation_confidence": "weak"}

    # The compound-title caution, carried over unchanged from `terms.classify`: a title
    # naming both a functional domain and a structural element cannot say which part the
    # alignment covered, so it resolves to the conservative reading. What is NEW is that a
    # term whose OWN roles include both is not a compound title -- it is one protein that
    # is genuinely both, and it keeps its direction.
    rb = [h for h in hits if "receptor_binding" in h["roles"]]
    struct = [h for h in hits if "structural_only" in h["roles"]
              and "receptor_binding" not in h["roles"]]
    if rb and struct:
        best = sorted(struct, key=lambda h: (_RANK[h["interpretation_confidence"]],
                                             -len(h["term"])))[0]
    else:
        best = sorted(hits, key=lambda h: (_DIRECTION_RANK[h["direction"]],
                                           _RANK[h["interpretation_confidence"]],
                                           -len(h["term"])))[0]
    roles = sorted({r for h in hits for r in h["roles"]})
    return {"matched": sorted(str(h["term"]) for h in hits),
            "roles": roles,
            "direction": best["direction"],
            "interpretation_confidence": best["interpretation_confidence"]}


def countable_strength(match_confidence, interpretation_confidence, rules):
    """The strength an EvidenceRecord may carry: the weaker of the two legs.

    A perfect match to a domain whose meaning is uncertain is a certain match to an
    uncertain thing. A confident interpretation of a marginal hit is a confident reading
    of noise. Neither deserves `strong`.
    """
    worse = max(_RANK.get(match_confidence, 2),
                _RANK.get(interpretation_confidence, 2))
    for name, rank in _RANK.items():
        if rank == worse:
            return name
    return "weak"


def match_confidence(evalue, rules, strong_max=1e-10):
    """The signature leg, and only that. Says a match happened, not what it means."""
    if evalue is None:
        return "weak"
    return "strong" if evalue <= strong_max else "moderate"


def disagreements_with_terms_module(rules, terms_module):
    """Where the shadow reading differs from the live one, term by term.

    Kept in the product rather than in a test so the comparison the migration depends on
    has one definition, and so it can be re-run against any future edit of either map.
    """
    out = []
    for entry in _index(rules):
        text = str(entry["term"])
        cat, _matched = terms_module.classify(text, rules)
        live = terms_module.direction_for(cat)
        shadow = classify(text, rules)
        if live != shadow["direction"]:
            out.append({"term": text, "live_direction": live,
                        "shadow_direction": shadow["direction"],
                        "roles": shadow["roles"],
                        "curated": bool(entry.get("curated"))})
    return out
