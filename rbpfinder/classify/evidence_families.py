"""De-correlation: count independent FAMILIES, never tools.

The anti-pattern this exists to prevent is reading "5 of 6 methods positive" as
five independent votes.
"""
import re

_STRENGTH_ORDER = {"strong": 0, "moderate": 1, "weak": 2}
_ANNOTATION_TOOLS = ("phold_prostt5", "pharokka_phrog")


def _phold_hit_is_phrog_derived(records, rules):
    """True when a phold hit may NOT be promoted to an independent T unit.

    Whitelist, not blacklist: only a target in a real structure database counts
    as structural. phold reports against several bundled sequence databases and
    the namespaces keep growing (protein<N>, envhog_*, WP_*), so enumerating
    them was unsustainable and each miss silently created a phantom T family.
    """
    pattern = rules.get("evidence_families.structure_database_tophit_pattern")
    for r in records:
        if r["tool"] == "phold_prostt5":
            hit = (r.get("raw") or {}).get("hit_id") or ""
            return not re.match(pattern, hit)
    return False


def _pdb_id(raw):
    """Normalise a PDB-ish identifier: 9RQI_C and 9rqi-assembly1.cif.gz_C -> 9rqi."""
    hit = str((raw or {}).get("hit_id") or "")
    head = re.split(r"[-_.]", hit, 1)[0].lower()
    return head if re.fullmatch(r"[0-9][a-z0-9]{3}", head) else None


def _supersede_same_template(records):
    """One template reached twice -- by profile and by structure -- is one datum.

    An HHpred hit to a PDB entry and a Foldseek hit to the same entry are the same
    template match seen two ways. The real-structure result supersedes the profile
    inference; the HHpred record is kept and marked, never deleted.
    """
    fs = {_pdb_id(r.get("raw")) for r in records if r["tool"] == "foldseek_structure"}
    fs.discard(None)
    if not fs:
        return
    for r in records:
        if r["tool"] == "hhpred" and _pdb_id(r.get("raw")) in fs:
            r["superseded_by"] = "foldseek_structure"


def decorrelate(records, rules):
    """Mark counts_as_independent on each record; return the independent families."""
    for r in records:
        r["counts_as_independent"] = False
        r.setdefault("superseded_by", None)


    _supersede_same_template(records)

    annotation_tool = next(
        (r["tool"] for r in records if r["tool"] in _ANNOTATION_TOOLS), None
    )
    if annotation_tool:
        for r in records:
            if r["tool"] == "c2_keywords":
                r["superseded_by"] = annotation_tool

    by_family = {}
    for r in records:
        if r["direction"] == "uninformative":
            continue
        if r["superseded_by"] is not None:
            continue
        by_family.setdefault(r["family"], []).append(r)

    independent = []
    for fam in sorted(by_family):
        best = sorted(
            by_family[fam],
            key=lambda r: (_STRENGTH_ORDER.get(r["strength"], 9), r["tool"]),
        )[0]
        best["counts_as_independent"] = True
        independent.append(fam)

    return independent, {
        "phold_hit_is_phrog_derived": _phold_hit_is_phrog_derived(records, rules),
    }


def counted(records):
    return [r for r in records if r.get("counts_as_independent")]
