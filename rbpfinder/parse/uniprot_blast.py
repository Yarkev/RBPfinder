"""Parse the EBI/UniProt blastp result table used throughout the SMA work.

Columns: Hit  DB  Accession  Description  Organism  Length  Score(Bits)
         Identities(%)  Positives(%)  E()

Note it carries NO query span, so evidence built from it is full-length. A
richer tabular format with qstart/qend supersedes it when one exists.
"""
import csv
import pathlib


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read(path):
    hits = []
    with pathlib.Path(path).open(encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            if not r.get("Accession"):
                continue
            hits.append({
                "rank": int(r.get("Hit") or len(hits) + 1),
                "accession": r["Accession"],
                "database": r.get("DB") or "UniProt",
                "description": (r.get("Description") or "").split(" OS=")[0].strip(),
                "description_full": r.get("Description") or "",
                "organism": r.get("Organism") or "",
                "target_length": _f(r.get("Length")),
                "score": _f(r.get("Score(Bits)")),
                "identity_pct": _f(r.get("Identities(%)")),
                "positives_pct": _f(r.get("Positives(%)")),
                "evalue": _f(r.get("E()")),
                "query_span": None,          # absent from this format
            })
    hits.sort(key=lambda h: h["rank"])
    return hits
