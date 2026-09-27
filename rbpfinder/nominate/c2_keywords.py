"""C2 annotation-keyword channel.

A keyword hit nominates; it never assigns a role. Note the de-correlation rule
in decision_rules.yaml: because the keyword is read out of the annotator's own
product string, it contributes no independent G unit -- it collapses into the
D-family record for the same CDS.
"""


def run(cds_rows, rules):
    terms = rules.get("stage1.channels.C2_annotation_keywords.positive_terms")
    noisy = {
        e["term"].lower()
        for e in rules.get("stage1.channels.C2_annotation_keywords.high_noise_terms", [])
    }
    nominated, matches = set(), {}
    for rec in cds_rows:
        p = (rec["product"] or "").lower()
        hits = sorted(t for t in terms if str(t).lower() in p)
        if hits:
            nominated.add(rec["cds_id"])
            matches[rec["cds_id"]] = {
                "terms": hits,
                "high_noise_only": all(h.lower() in noisy for h in hits),
            }
    return {"nominated": nominated, "matches": matches}
