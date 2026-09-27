"""Parse an InterProScan JSON result.

Zero matches is a normal, informative outcome for phage structural proteins and
is returned as an empty list -- the caller turns that into an `uninformative`
record, never into counter-evidence.
"""
import json
import pathlib


def read(path):
    text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        # an empty export is a real "zero matches" result, not a parse failure
        return []
    d = json.loads(text)
    results = d.get("results", d if isinstance(d, list) else [])
    out = []
    for res in results:
        for m in res.get("matches", []):
            sig = m.get("signature") or {}
            entry = sig.get("entry") or {}
            for loc in m.get("locations", []) or [{}]:
                out.append({
                    "accession": sig.get("accession"),
                    "name": sig.get("name") or "",
                    "type": sig.get("type") or "",
                    "library": ((sig.get("signatureLibraryRelease") or {}).get("library")),
                    "entry_accession": entry.get("accession"),
                    "entry_name": entry.get("name") or "",
                    "entry_description": entry.get("description") or "",
                    "start_aa": loc.get("start"),
                    "end_aa": loc.get("end"),
                    "evalue": loc.get("evalue", m.get("evalue")),
                    "score": loc.get("score", m.get("score")),
                })
    out.sort(key=lambda x: (x.get("evalue") if x.get("evalue") is not None else 1e9,
                            str(x.get("accession"))))
    return out
