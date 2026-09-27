"""Parse a Foldseek web-server JSON result.

Shape:  {"results": [{"db": "pdb100", "alignments": [[hit, ...]]}, ...]}

The target string carries both the identifier and the structure's title, e.g.

    3oea-assembly1.cif.gz_A Crystal structure of ... CBM16 ...

so the functional identity that Stage 3 needs is recoverable without any
external lookup. Query spans are kept: the point of a structural hit on a large
tail fibre is usually that it lands on one terminal domain.
"""
import json
import pathlib


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _split_target(target):
    """Return (identifier, description)."""
    t = str(target or "")
    if " " in t:
        ident, desc = t.split(" ", 1)
        return ident.strip(), desc.strip()
    return t.strip(), ""


def read(path):
    d = json.loads(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    out = []
    for block in d.get("results", []):
        db = block.get("db")
        alns = block.get("alignments") or []
        flat = alns[0] if alns and isinstance(alns[0], list) else alns
        for rank, a in enumerate(flat, 1):
            ident, desc = _split_target(a.get("target"))
            out.append({
                "rank": rank,
                "database": db,
                "target": ident,
                "description": desc,
                "probability": _f(a.get("prob")),
                "evalue": _f(a.get("eval")),
                "score": _f(a.get("score")),
                "seq_identity": _f(a.get("seqId")),
                "query_span": (a.get("qStartPos"), a.get("qEndPos")),
                "target_span": (a.get("dbStartPos"), a.get("dbEndPos")),
                "query_length": a.get("qLen"),
                "taxon": a.get("taxName") or "",
            })
    return out


def by_database(hits):
    out = {}
    for h in hits:
        out.setdefault(h["database"], []).append(h)
    return out
