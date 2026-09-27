"""C4 tandem-cluster channel.

Two linkage rules in UNION (never intersection): an adaptive intergenic gap
scaled to this genome's own distribution, and a plain +/-2 CDS neighbourhood
that survives large gaps and mis-called intervening ORFs. If any member is
nominated by any channel, the whole cluster enters.
"""


def _percentile(values, pct):
    if not values:
        return 0
    s = sorted(values)
    idx = min(int(round((pct / 100.0) * (len(s) - 1))), len(s) - 1)
    return s[idx]


def _gaps(cds_rows):
    return [
        max(cds_rows[i + 1]["start_nt"] - cds_rows[i]["end_nt"] - 1, 0)
        for i in range(len(cds_rows) - 1)
    ]


def run(cds_rows, rules, already_nominated, is_circular=False):
    base = "stage1.channels.C4_tandem_cluster.linkage_rules"
    lr = {r["id"]: r for r in rules.get(base)}
    floor = lr["adaptive_gap"]["absolute_floor_bp"]
    pct = lr["adaptive_gap"]["gap_percentile"]

    gaps = _gaps(cds_rows)
    threshold = max(floor, _percentile(gaps, pct))

    n = len(cds_rows)
    clusters, cur = [], [0]
    for i in range(n - 1):
        same_strand = cds_rows[i]["strand"] == cds_rows[i + 1]["strand"]
        if same_strand and gaps[i] <= threshold:
            cur.append(i + 1)
        else:
            clusters.append(cur)
            cur = [i + 1]
    clusters.append(cur)

    if is_circular and len(clusters) > 1:
        wrap_gap = max(cds_rows[0]["start_nt"] - cds_rows[-1]["end_nt"] - 1, 0)
        if cds_rows[0]["strand"] == cds_rows[-1]["strand"] and wrap_gap <= threshold:
            clusters[0] = clusters[-1] + clusters[0]
            clusters.pop()

    idx_of = {rec["cds_id"]: i for i, rec in enumerate(cds_rows)}
    nominated, detail, warnings = set(), {}, []
    cap = rules.get("stage1.channels.C4_tandem_cluster.max_cluster_cds")
    cap_warning = rules.get(
        "stage1.channels.C4_tandem_cluster.on_cluster_exceeds_cap.emit_warning"
    )

    for cl in clusters:
        ids = [cds_rows[i]["cds_id"] for i in cl]
        if not any(i in already_nominated for i in ids):
            continue
        if len(ids) > cap:
            warnings.append(
                "%s: cluster of %d CDS (%s..%s) exceeds max_cluster_cds=%d; "
                "not bulk-added, falling back to +/-2 neighbourhood"
                % (cap_warning, len(ids), ids[0], ids[-1], cap)
            )
            continue
        for cid in ids:
            nominated.add(cid)
            detail.setdefault(cid, []).append("cluster of %d" % len(ids))

    for cid in sorted(already_nominated):
        i = idx_of.get(cid)
        if i is None:
            continue
        for j in range(max(i - 2, 0), min(i + 3, n)):
            nid = cds_rows[j]["cds_id"]
            if nid not in already_nominated:
                nominated.add(nid)
                detail.setdefault(nid, []).append("within +/-2 CDS of %s" % cid)

    return {
        "nominated": nominated,
        "warnings": warnings,
        "gap_threshold_bp": threshold,
        "gap_percentile_value": _percentile(gaps, pct),
        "detail": detail,
    }
