"""Parse phold_per_cds_predictions.tsv into normalised CDS records."""
import csv
import pathlib


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _int(v):
    f = _num(v)
    return None if f is None else int(f)


CATEGORY_PROPORTION_SUFFIX = "_bitscore_proportion"


def read(path):
    rows = []
    with pathlib.Path(path).open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            start, end = _int(r["start"]), _int(r["end"])
            lo, hi = min(start, end), max(start, end)
            qlen = _int(r.get("qLen"))
            rec = {
                "cds_id": r["cds_id"],
                "contig_id": r.get("contig_id"),
                "start_nt": lo,
                "end_nt": hi,
                "strand": r.get("strand"),
                "phrog": None if r.get("phrog") in (None, "", "No_PHROG") else r["phrog"],
                "function": r.get("function") or "unknown function",
                "product": r.get("product") or "",
                "annotation_method": r.get("annotation_method") or "none",
                "annotation_source": r.get("annotation_source") or "none",
                "annotation_confidence": r.get("annotation_confidence") or "none",
                "bitscore": _num(r.get("bitscore")),
                "fident": _num(r.get("fident")),
                "evalue": _num(r.get("evalue")),
                "qstart": _int(r.get("qStart")),
                "qend": _int(r.get("qEnd")),
                "qlen": qlen,
                "qcov": _num(r.get("qCov")),
                "tophit_protein": r.get("tophit_protein") or None,
                "prostt5_confidence": _num(r.get("prostt5_confidence")),
            }
            rec["length_aa"] = qlen if qlen else max((hi - lo + 1) // 3 - 1, 1)
            rec["category_proportions"] = {
                k[: -len(CATEGORY_PROPORTION_SUFFIX)]: _num(v) or 0.0
                for k, v in r.items()
                if k.endswith(CATEGORY_PROPORTION_SUFFIX)
            }
            rows.append(rec)
    rows.sort(key=lambda x: (x["start_nt"], x["cds_id"]))
    return rows
