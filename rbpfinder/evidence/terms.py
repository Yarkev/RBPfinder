"""Classify a homolog's annotation text against annotation_term_map.

The map lives in decision_rules.yaml. This module only applies it -- including WHICH
terms are matched as whole tokens rather than as substrings. The list is
`stage2.annotation_term_map.token_match_terms` and this file must never contain the
terms themselves: a rule split across code and configuration has two sources of truth,
and the one in the code is the one nobody edits.
"""
import re

POSITIVE = "positive_rbp"
APPARATUS = "apparatus"
STRUCTURAL = "structural_only"
NOISE = "high_noise"
UNINFORMATIVE = "uninformative"

RANK = {POSITIVE: 0, STRUCTURAL: 1, APPARATUS: 2, NOISE: 3, UNINFORMATIVE: 4}


_TOKEN_RE = {}


def _token_pattern(term):
    """Whole-token match with non-alphanumeric boundaries.

    Not ``: protein annotations are full of hyphens, slashes, parentheses and commas,
    and those should delimit a name rather than hide it. `(Tal)`, `Tal/Dit` and
    `baseplate-Tal` all match; `porTAL` and `caTALytic` do not.
    """
    if term not in _TOKEN_RE:
        _TOKEN_RE[term] = re.compile(
            r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(str(term)), re.I)
    return _TOKEN_RE[term]


def _has(text, terms, token_terms=()):
    """Which of `terms` occur in `text`.

    Substring matching, unchanged, except for the terms the RULES declare as token
    matches. Case-insensitive either way -- the regex carries re.I so that introducing
    it changes the boundary semantics and nothing else.
    """
    t = (text or "").lower()
    tok = {str(x).lower() for x in token_terms}
    out = []
    for x in terms:
        if str(x).lower() in tok:
            if _token_pattern(x).search(text or ""):
                out.append(x)
        elif str(x).lower() in t:
            out.append(x)
    return out


def classify(text, rules):
    """Return (category, matched_terms).

    A title naming BOTH a functional domain and a structural element is a
    compound protein, and the title alone cannot say which part the alignment
    covered. Such a title resolves to the conservative reading. Disambiguating
    it needs the query-side domain layout, which is Stage 3c work, not a
    keyword rule.

    CORRECTED 2026-09-02 (M20-b2D1). This docstring used to cite N136 CDS0100 as
    the case the rule covers and to call it "a standing hard negative". Both
    halves were wrong, and measuring finally showed it:

      * The guard CANNOT fire on that record. It requires a structural_only term
        in the text, and the HHpred title is "Pulmonary surfactant-associated
        protein D; collectin, c-type lectin, alpha-helical coiled coil,
        carbohydrate recognition domain". `collectin` is not `collagen`, and the
        word `collagen` never appears. The reasoning was about the BIOLOGY -- a
        collectin is collagen plus lectin -- while the rule matches on WORDS.
        The record still reads supports_receptor_binding, from the bare `lectin`.

      * It is not a hard negative. `benchmark/SMA_ambiguous.tsv` labels it
        `ambiguous_rbp_associated`, `ground_truth_rbp=false`, and explicitly
        `include_in_hard_negative_regression=false`. The adjudication saw the
        96.4% collectin / C-type lectin hit and still called it ambiguous rather
        than negative.

    A maintainer reading the old text would have believed a contract that does
    not exist and fixed the wrong thing. The behaviour is unchanged here; what
    `lectin` should mean is M20-b2D2, and it is still shadow.
    """
    base = "stage2.annotation_term_map"
    noise_terms = [e["term"] for e in rules.get(base + ".high_noise_terms")]
    # Required, deliberately. A missing key raises MissingRule rather than defaulting to
    # "no token terms" -- a silent fallback to substring matching would resurrect the
    # 1757-record false-positive defect the moment the key went missing from a package.
    token_terms = rules.get(base + ".token_match_terms")

    pos = _has(text, rules.get(base + ".positive_rbp_terms"), token_terms)
    struct = _has(text, rules.get(base + ".structural_only_terms"), token_terms)
    if pos and struct:
        return STRUCTURAL, sorted(set(pos + struct))
    if pos:
        return POSITIVE, pos
    if struct:
        return STRUCTURAL, struct
    app = _has(text, rules.get(base + ".apparatus_terms"), token_terms)
    if app:
        return APPARATUS, app
    noise = _has(text, noise_terms, token_terms)
    if noise:
        # A bare 'hydrolase' never counts toward positive consensus on its own.
        # W073 CDS0004 is the standing regression case for exactly this.
        return NOISE, noise
    return UNINFORMATIVE, _has(text, rules.get(base + ".uninformative_terms"), token_terms)


def direction_for(category):
    return {
        POSITIVE: "supports_receptor_binding",
        APPARATUS: "supports_apparatus_membership",
        STRUCTURAL: "supports_structural_only",
    }.get(category, "uninformative")
