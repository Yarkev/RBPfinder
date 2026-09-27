"""Parse an HHsearch/HHpred .hhr summary table.

Only the hit table is read; that carries probability, E-value and -- importantly
for region localisation -- the query HMM span.
"""
import pathlib
import re

_ROW = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+(.*?)\s+"
    r"(\d+\.\d+)\s+(\S+)\s+(\S+)\s+"
    r"(\S+)\s+(\S+)\s+(\d+)\s+"
    r"(\d+)-(\d+)\s+(\d+)-(\d+)"
)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


_BLOCK = re.compile(r"^>(\S+)\s*(.*)$")
_PROB = re.compile(r"Probab=\s*(\d+\.?\d*)")


def _full_descriptions(text):
    """Full titles from the '>' alignment blocks, keyed by (hit_id, probability).

    The summary table truncates the title to ~30 characters. That matters: PDB
    9RQI reads "Tail fiber protein; lip" in the table, while its real title
    continues "... receptor-binding protein ...". Classifying the truncated form
    loses the only functional word in it.
    """
    out, pending = {}, None
    for line in text.splitlines():
        m = _BLOCK.match(line)
        if m:
            pending = (m.group(1), m.group(2).strip())
            continue
        if pending:
            p = _PROB.search(line)
            if p:
                # the table rounds to one decimal (98.5) while the block keeps two
                # (Probab=98.48), so the key must be rounded to match
                out[(pending[0], round(float(p.group(1)), 1))] = pending[1]
                pending = None
    return out


def read(path):
    text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    full = _full_descriptions(text)
    hits, in_table = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("No Hit"):
            in_table = True
            continue
        if in_table:
            if not line.strip():
                if hits:
                    break
                continue
            m = _ROW.match(line)
            if not m:
                continue
            hit_id, prob = m.group(2), _f(m.group(4))
            desc = full.get((hit_id, round(prob, 1) if prob is not None else prob))                 or m.group(3).strip()
            hits.append({
                "rank": int(m.group(1)),
                "hit_id": hit_id,
                "description": desc,
                "description_table": m.group(3).strip(),
                "probability_pct": _f(m.group(4)),
                "evalue": _f(m.group(5)),
                "score": _f(m.group(7)),
                "cols": int(m.group(9)),
                "query_span": (int(m.group(10)), int(m.group(11))),
                "template_span": (int(m.group(12)), int(m.group(13))),
            })
    hits.sort(key=lambda h: h["rank"])
    return hits
