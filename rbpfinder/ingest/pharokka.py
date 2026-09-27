"""Parse pharokka's merged CDS table.

Used only to cross-check gene calls and to supply a length for CDS that phold
left unannotated. The phold table is authoritative for annotation.
"""
import csv
import pathlib


def read(path):
    out = {}
    with pathlib.Path(path).open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            gene = r.get("gene")
            if not gene:
                continue
            start, stop = int(r["start"]), int(r["stop"])
            lo, hi = min(start, stop), max(start, stop)
            out[gene] = {
                "start_nt": lo,
                "end_nt": hi,
                "strand": r.get("strand"),
                "length_aa": max((hi - lo + 1) // 3 - 1, 1),
                "annot": r.get("annot") or "",
                "category": r.get("category") or "",
                "method": r.get("Method") or "",
            }
    return out


def reconcile(cds_rows, pharokka_map):
    """Fill gaps and record disagreements. Never silently overwrites phold."""
    notes = []
    for rec in cds_rows:
        p = pharokka_map.get(rec["cds_id"])
        if not p:
            notes.append("%s: present in phold, absent from pharokka table" % rec["cds_id"])
            continue
        if not rec.get("qlen"):
            rec["length_aa"] = p["length_aa"]
        if p["start_nt"] != rec["start_nt"] or p["end_nt"] != rec["end_nt"]:
            notes.append(
                "%s: coordinate mismatch phold %d-%d vs pharokka %d-%d"
                % (rec["cds_id"], rec["start_nt"], rec["end_nt"], p["start_nt"], p["end_nt"])
            )
    for gene in sorted(set(pharokka_map) - {r["cds_id"] for r in cds_rows}):
        notes.append("%s: present in pharokka table, absent from phold" % gene)
    return notes
