"""C3 dark-matter channel -- the highest-recall-value channel.

Every unannotated CDS inside the C1 window enters unconditionally. Phold cannot
carry this channel: measured on NC_043029, zero of 357 unknown-function CDS had
a tail-category bitscore signal (benchmark/q6_cloud_probe/FINDINGS.md).
"""


def is_dark(rec):
    return (
        rec.get("phrog") is None
        or rec.get("function") == "unknown function"
        or "hypothetical" in (rec.get("product") or "").lower()
    )


def run(cds_rows, rules, c1_window_ids):
    nominated = {
        rec["cds_id"]
        for rec in cds_rows
        if rec["cds_id"] in c1_window_ids and is_dark(rec)
    }
    return {"nominated": nominated}
