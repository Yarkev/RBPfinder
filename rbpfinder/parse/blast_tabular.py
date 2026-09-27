"""Parse BLAST+ tabular output (outfmt 6) with an explicit column list.

The column set RBPfinder asks for is

    qseqid sseqid pident length qstart qend sstart send evalue bitscore qcovs stitle

`qstart`/`qend` are the reason to prefer this over the older EBI result tables:
those carried no query span, so every S-family record had to claim the full
length. A local BLAST result can localise its evidence to a region, which is
what the schema wants and what matters on a long tail fibre whose receptor
-binding domain is only the terminal fifth.
"""
import csv
import pathlib

from . import accession

COLUMNS = ["qseqid", "sseqid", "pident", "length", "qstart", "qend",
           "sstart", "send", "evalue", "bitscore", "qcovs", "stitle"]

OUTFMT = "6 " + " ".join(COLUMNS)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    f = _f(v)
    return None if f is None else int(f)


def read(path, columns=None):
    """Return {query_id: [hit, ...]} preserving BLAST's own ordering."""
    cols = columns or COLUMNS
    out = {}
    with pathlib.Path(path).open(encoding="utf-8", errors="replace", newline="") as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if not row or len(row) < len(cols):
                continue
            r = dict(zip(cols, row))
            # stitle repeats the id; strip it so the description classifies cleanly
            title = r.get("stitle") or ""
            if title.startswith(r["sseqid"]):
                title = title[len(r["sseqid"]):].strip()
            hits = out.setdefault(r["qseqid"], [])
            hits.append({
                "rank": len(hits) + 1,
                # Normalised so a local `-parse_seqids` database and a remote
                # provider name the same protein with the same string. The raw id is
                # kept beside it: normalising must not destroy what the tool returned.
                "accession": accession.subject_identity(r["sseqid"]),
                "accession_full": r["sseqid"],
                "database": "local_blast",
                "description": title,
                "description_full": r.get("stitle") or "",
                "organism": "",
                "identity_pct": _f(r.get("pident")),
                "evalue": _f(r.get("evalue")),
                "score": _f(r.get("bitscore")),
                "coverage_pct": _f(r.get("qcovs")),
                "query_span": (_i(r.get("qstart")), _i(r.get("qend"))),
                "target_span": (_i(r.get("sstart")), _i(r.get("send"))),
                "alignment_length": _i(r.get("length")),
            })
    return out


def _cell(v):
    """Tabs and newlines inside stitle would silently re-shape the row on re-read."""
    if v is None:
        return ""
    return str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ")


def write(path, hits_by_query):
    """Serialise parsed hits back to outfmt-6, so a search becomes a replayable file.

    read(write(hits)) must return the same hits: the benchmark artefacts are the
    audit trail, and an artefact that cannot be re-read is not evidence of anything.
    """
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline="\n") as fh:
        for qid, hits in hits_by_query.items():
            for h in hits:
                qs = h.get("query_span") or (None, None)
                ts = h.get("target_span") or (None, None)
                fh.write("\t".join(_cell(v) for v in [
                    qid, h.get("accession"), h.get("identity_pct"),
                    h.get("alignment_length"), qs[0], qs[1], ts[0], ts[1],
                    h.get("evalue"), h.get("score"), h.get("coverage_pct"),
                    h.get("description_full"),
                ]) + "\n")
    return p
