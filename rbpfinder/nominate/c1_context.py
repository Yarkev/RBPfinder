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

Short gene-name anchors (`Tal`, `Dit`) are matched as whole tokens, using the same
`stage2.annotation_term_map.token_match_terms` list the homolog reader uses. As plain
substrings they made "meTALlopeptidase", "caTALytic subunit" and "RNA eDITing protein"
STRONG tail anchors, each opening a full window in the middle of a replication module.

Circular genomes: `decision_rules.yaml` requires circular adjacency for `c1_window`,
because a tail module split across the origin must still form ONE window. C4 and the A3
audit already honoured that; this channel did not, so an origin-spanning tail module
produced two truncated windows that the linear merge (`lo <= hi + 1`) could never join.

Implementation: the coordinate system is ROTATED so the cut falls in the largest circular
gap between consecutive anchors -- by construction no anchor cluster straddles the cut,
so the tested linear algorithm then runs unchanged in rotated space (indices may leave
[0, n); every row access goes through `at()`, which is mod n) and the result is mapped
back. The linear path is byte-for-byte the previous behaviour.

Known limitation: window merging still happens in rotated linear space. If two windows
BOTH extend across the cut they overlap on the circle without being merged. That needs
the largest anchor-free gap to be smaller than the two extensions that reach into it,
which is rare; the origin-splitting defect this fixes is not.
"""
from ..evidence import terms as _terms


def _hits(product, terms, token_terms=()):
    return _terms._has(product, terms, token_terms)


def _classify(product, strong, weak, boundary, token_terms=()):
    if _hits(product, boundary, token_terms):
        return "boundary"
    if _hits(product, strong, token_terms):
        return "strong"
    if _hits(product, weak, token_terms):
        return "weak"
    return None


def _abandoned(rules, base):
    return {
        "nominated": set(),
        "windows": [],
        "clusters": 0,
        "warning": rules.get(base + ".on_all_anchors_failed.emit_warning"),
        "detail": "no anchor matched any product string",
    }


def run(cds_rows, rules, is_circular=False, genome_length=None):
    """Nominate the tail-module window(s).

    `is_circular` must be the RESOLVED circularity of the genome -- the GenBank's own
    topology when the user did not override it -- not the raw command-line flag. A record
    that declares itself circular and is analysed as linear silently loses this channel's
    origin handling, which is the whole point of the fix.

    `genome_length` is used for circular nt distances. It falls back to the largest CDS
    end coordinate, which underestimates the genome slightly and therefore errs toward
    extending the window -- the safe direction for a recall channel, but the real value
    should be passed when the caller has it.
    """
    base = "stage1.channels.C1_module_position"
    strong = rules.get(base + ".anchors_strong")
    weak = rules.get(base + ".anchors_weak")
    boundary = rules.get(base + ".anchors_boundary")
    gap_cds = rules.get(base + ".cluster_gap_cds")
    ext_strong = rules.get(base + ".window_extension_cds_strong")
    ext_weak = rules.get(base + ".window_extension_cds_weak")
    ext_kb = rules.get(base + ".window_extension_kb")
    truncate = rules.get(base + ".boundary_truncates_window")

    token_terms = rules.get("stage2.annotation_term_map.token_match_terms")

    n = len(cds_rows)
    kinds = [_classify(r["product"], strong, weak, boundary, token_terms)
             for r in cds_rows]
    anchor_idx = [i for i, k in enumerate(kinds) if k in ("strong", "weak")]

    if not anchor_idx:
        return _abandoned(rules, base)

    circular = bool(is_circular) and n > 1

    # --- rotation offset: cut the circle at the largest circular gap between
    # consecutive anchors, so no anchor cluster straddles the cut.
    r = 0
    if circular:
        m = len(anchor_idx)
        if m == 1:
            r = anchor_idx[0] % n
        else:
            gaps = [(anchor_idx[(k + 1) % m] - anchor_idx[k]) % n for k in range(m)]
            kmax = max(range(m), key=lambda k: gaps[k])
            r = anchor_idx[(kmax + 1) % m] % n

    def at(j):
        """Rotated-space index -> original CDS index."""
        return (r + j) % n

    # --- cluster anchors by genomic distance, in CDS units (rotated space)
    anchors_rot = sorted((a - r) % n for a in anchor_idx)
    clusters, cur = [], [anchors_rot[0]]
    for i in anchors_rot[1:]:
        if i - cur[-1] <= gap_cds:
            cur.append(i)
        else:
            clusters.append(cur)
            cur = [i]
    clusters.append(cur)

    starts = [c["start_nt"] for c in cds_rows]
    ends = [c["end_nt"] for c in cds_rows]
    budget = int(ext_kb * 1000)
    span = int(genome_length) if genome_length else (max(ends) if ends else 0)

    # --- one window per cluster
    raw_windows = []
    for cl in clusters:
        has_strong = any(kinds[at(i)] == "strong" for i in cl)
        ext = ext_strong if has_strong else ext_weak
        lo, hi = min(cl), max(cl)

        if circular:
            # CDS-count extension first (rotated indices may leave [0, n)), then the kb
            # extension as a UNION, mirroring the linear semantics: a CDS joins the
            # window when it lies within ext_kb of the cluster edge, measured circularly.
            lo_i, hi_i = lo - ext, hi + ext
            edge_nt = starts[at(lo)]
            steps = 0
            while steps < n and span:
                if (edge_nt - ends[at(lo_i - 1)]) % span > budget:
                    break
                lo_i -= 1
                steps += 1
            edge_nt = ends[at(hi)]
            steps = 0
            while steps < n and span:
                if (starts[at(hi_i + 1)] - edge_nt) % span > budget:
                    break
                hi_i += 1
                steps += 1
        else:
            # CDS-count extension and kb extension are applied as a UNION
            lo_i, hi_i = max(lo - ext, 0), min(hi + ext, n - 1)
            lo_nt = starts[lo] - budget
            hi_nt = ends[hi] + budget
            while lo_i > 0 and ends[lo_i - 1] >= lo_nt:
                lo_i -= 1
            while hi_i < n - 1 and starts[hi_i + 1] <= hi_nt:
                hi_i += 1

        # --- a boundary anchor ends the window; the cluster itself is never truncated
        if truncate:
            for j in range(lo - 1, lo_i - 1, -1):
                if kinds[at(j)] == "boundary":
                    lo_i = j + 1
                    break
            for j in range(hi + 1, hi_i + 1):
                if kinds[at(j)] == "boundary":
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
            nominated.add(cds_rows[at(k)]["cds_id"])
        windows.append({
            "start_cds": cds_rows[at(w["lo"])]["cds_id"],
            "end_cds": cds_rows[at(w["hi"])]["cds_id"],
            "n_cds": min(w["hi"] - w["lo"] + 1, n),
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
