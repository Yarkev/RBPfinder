"""C2 annotation-keyword channel.

A keyword hit nominates; it never assigns a role. Note the de-correlation rule
in decision_rules.yaml: because the keyword is read out of the annotator's own
product string, it contributes no independent G unit -- it collapses into the
D-family record for the same CDS.

`Tal` and `Dit` are whole-token matches (`stage2.annotation_term_map.token_match_terms`);
as substrings they nominated every "porTAL protein" and "caTALytic subunit".
"""
from ..evidence import terms as _terms


def run(cds_rows, rules):
    terms = rules.get("stage1.channels.C2_annotation_keywords.positive_terms")
    noisy = {
        e["term"].lower()
        for e in rules.get("stage1.channels.C2_annotation_keywords.high_noise_terms", [])
    }
    token_terms = rules.get("stage2.annotation_term_map.token_match_terms")
    nominated, matches = set(), {}
    for rec in cds_rows:
        hits = sorted(_terms._has(rec["product"], terms, token_terms))
        if hits:
            nominated.add(rec["cds_id"])
            matches[rec["cds_id"]] = {
                "terms": hits,
                "high_noise_only": all(h.lower() in noisy for h in hits),
            }
    return {"nominated": nominated, "matches": matches}
