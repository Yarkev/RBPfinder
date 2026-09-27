"""C1 module-position channel (family G), multi-window.

"Find the tail module" is not "find a stretch near the first tail anchor". Myo
and jumbo genomes carry RBP loci in several places, so every anchor cluster in
the genome gets its own window and the union enters P.

Anchor strength keeps that from degenerating: a strong anchor (tape measure,
sheath, baseplate, an explicit fibre/spike) opens a full window, while a generic
"tail protein" on its own opens only a small local one.

If all anchors fail the channel is ABANDONED with a warning; it never degrades
to "start at CDS1", which is how the v0 awk labelled portal and major head
protein as accessory RBP candidates.
"""


def _hits(product, terms):
    p = (product or "").lower()
    return [t for t in terms if str(t).lower() in p]


def _classify(product, strong, weak, boundary):
    if _hits(product, boundary):
        return "boundary"
    if _hits(product, strong):
        return "strong"
    if _hits(product, weak):
        return "weak"
    return None


def run(cds_rows, rules):
    base = "stage1.channels.C1_module_position"
    strong = rules.get(base + ".anchors_strong")
    weak = rules.get(base + ".anchors_weak")
    boundary = rules.get(base + ".anchors_boundary")
    gap_cds = rules.get(base + ".cluster_gap_cds")
    ext_strong = rules.get(base + ".window_extension_cds_strong")
    ext_weak = rules.get(base + ".window_extension_cds_weak")
    ext_kb = rules.get(base + ".window_extension_kb")
    truncate = rules.get(base + ".boundary_truncates_window")

    n = len(cds_rows)
    kinds = [_classify(r["product"], strong, weak, boundary) for r in cds_rows]
    anchor_idx = [i for i, k in enumerate(kinds) if k in ("strong", "weak")]

    if not anchor_idx:
        return {
            "nominated": set(),
            "windows": [],
            "clusters": 0,
            "warning": rules.get(base + ".on_all_anchors_failed.emit_warning"),
            "detail": "no anchor matched any product string",
        }

    # --- cluster anchors by genomic distance, in CDS units
    clusters, cur = [], [anchor_idx[0]]
    for i in anchor_idx[1:]:
        if i - cur[-1] <= gap_cds:
            cur.append(i)
        else:
            clusters.append(cur)
            cur = [i]
    clusters.append(cur)

    # --- one window per cluster
    raw_windows = []
    for cl in clusters:
        has_strong = any(kinds[i] == "strong" for i in cl)
        ext = ext_strong if has_strong else ext_weak
        lo, hi = min(cl), max(cl)

        # CDS-count extension and kb extension are applied as a UNION
        lo_i, hi_i = max(lo - ext, 0), min(hi + ext, n - 1)
        lo_nt = cds_rows[lo]["start_nt"] - int(ext_kb * 1000)
        hi_nt = cds_rows[hi]["end_nt"] + int(ext_kb * 1000)
        while lo_i > 0 and cds_rows[lo_i - 1]["end_nt"] >= lo_nt:
            lo_i -= 1
        while hi_i < n - 1 and cds_rows[hi_i + 1]["start_nt"] <= hi_nt:
            hi_i += 1

        # --- a boundary anchor ends the window; the cluster itself is never truncated
        if truncate:
            for j in range(lo - 1, lo_i - 1, -1):
                if kinds[j] == "boundary":
                    lo_i = j + 1
                    break
            for j in range(hi + 1, hi_i + 1):
                if kinds[j] == "boundary":
                    hi_i = j - 1
                    break

        raw_windows.append({
            "lo": lo_i, "hi": hi_i,
            "anchor_lo": lo, "anchor_hi": hi,
            "strength": "strong" if has_strong else "weak",
            "n_anchors": len(cl),
        })

    # --- merge overlapping / touching windows
    raw_windows.sort(key=lambda w: (w["lo"], w["hi"]))
    merged = [dict(raw_windows[0])]
    for w in raw_windows[1:]:
        if w["lo"] <= merged[-1]["hi"] + 1:
            m = merged[-1]
            m["hi"] = max(m["hi"], w["hi"])
            m["n_anchors"] += w["n_anchors"]
            if w["strength"] == "strong":
                m["strength"] = "strong"
        else:
            merged.append(dict(w))

    nominated = set()
    windows = []
    for w in merged:
        for k in range(w["lo"], w["hi"] + 1):
            nominated.add(cds_rows[k]["cds_id"])
        windows.append({
            "start_cds": cds_rows[w["lo"]]["cds_id"],
            "end_cds": cds_rows[w["hi"]]["cds_id"],
            "n_cds": w["hi"] - w["lo"] + 1,
            "strength": w["strength"],
            "n_anchors": w["n_anchors"],
        })

    return {
        "nominated": nominated,
        "windows": windows,
        "clusters": len(clusters),
        "warning": None,
        "detail": "%d anchor cluster(s) -> %d window(s), %d CDS: %s"
                  % (len(clusters), len(windows), len(nominated),
                     "; ".join("%s..%s(%d,%s)" % (w["start_cds"].rsplit("_", 1)[-1],
                                                  w["end_cds"].rsplit("_", 1)[-1],
                                                  w["n_cds"], w["strength"])
                               for w in windows)),
    }
