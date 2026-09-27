"""all_CDS_screening.tsv -- one row for EVERY CDS in the genome.

Its purpose is post-mortem: when a real RBP is later found to have been missed,
this table must be able to answer "why was CDS_0041 not a candidate?". The
`reason` field is therefore never empty.
"""

COLUMNS = [
    "cds_id", "start_nt", "end_nt", "strand", "length_aa", "annotation", "phrog",
    "module", "nomination_channels", "phold_call", "families_positive",
    "is_candidate", "tier", "reason",
]


def module_of(rec, rules):
    mm = rules.get("module_map")
    return mm.get(rec["function"], mm.get("_default"))


def _reason_not_candidate(rec, c1, c2_matches):
    bits = []
    if c1.get("windows"):
        bits.append(
            "outside all %d C1 window(s) [%s]"
            % (len(c1["windows"]),
               "; ".join("%s..%s" % (w["start_cds"], w["end_cds"])
                         for w in c1["windows"][:4]))
        )
    else:
        bits.append("C1 abandoned: %s" % c1.get("warning"))
    bits.append(
        "keyword match" if rec["cds_id"] in c2_matches else "no C2 keyword in product"
    )
    bits.append("not within +/-2 CDS of any nominated CDS and not in a nominated cluster")
    return "; ".join(bits)


def build(cds_rows, pool, tiers_by_id, channels_by_id, families_by_id, rules, c1, c2_matches):
    rows = []
    for rec in cds_rows:
        cid = rec["cds_id"]
        is_cand = cid in pool
        chans = sorted(channels_by_id.get(cid, []))
        tier = tiers_by_id.get(cid)
        tier_s = ("%s-%s" % (tier["tier_r"], tier["tier_e"])) if tier else None
        if is_cand:
            reason = "nominated by %s; %s" % (",".join(chans), tier_s)
            if tier and tier["hard_contradiction"]:
                reason += "; hard_exclusion on positive non-RBP identity"
        else:
            reason = _reason_not_candidate(rec, c1, c2_matches)
        rows.append({
            "cds_id": cid,
            "start_nt": rec["start_nt"],
            "end_nt": rec["end_nt"],
            "strand": rec["strand"],
            "length_aa": rec["length_aa"],
            "annotation": rec["product"],
            "phrog": rec["phrog"],
            "module": module_of(rec, rules),
            "nomination_channels": chans,
            "phold_call": rec["product"] if rec["annotation_method"] == "foldseek" else None,
            "families_positive": families_by_id.get(cid, []),
            "is_candidate": is_cand,
            "tier": tier_s,
            "reason": reason,
        })
    return rows


def write_tsv(rows, path):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(COLUMNS) + "\n")
        for r in rows:
            vals = []
            for c in COLUMNS:
                v = r.get(c)
                if isinstance(v, list):
                    v = ",".join(map(str, v))
                elif isinstance(v, bool):
                    v = "yes" if v else "no"
                vals.append("" if v is None else str(v))
            fh.write("\t".join(vals) + "\n")
