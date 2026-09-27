"""Parse EBI Job Dispatcher BLAST JSON into the project's hit shape.

Why not reuse `uniprot_blast.py`: that parser reads the old summary table, whose own
docstring records the defect -- it carries no query span, so every S record built from
it claims the full protein length. Local BLAST localises evidence to a span, and that
span is not cosmetic: W073cp2a2_CDS_0005's sialidase match sits at 140-208 of a much
longer protein, and `rbp_evidence_localisation` reports it.

So the remote backend consumes the coordinate-bearing JSON, and a response without
coordinates is REJECTED rather than degraded. `remote_stage2.result_format` freezes
that; the alternative -- shipping the summary table to get something working -- would
make the remote backend permanently weaker on a dimension already shown to matter.

The output dicts are the same shape `blast_tabular.read` produces, because both feed
`local_files._merge_sequence_hits` and then a single `sequence_homology.build`. Two
parsers, one evidence vocabulary.
"""
from . import accession
from ..errors import InputValidationError

REQUIRED_HSP_FIELDS = ("hsp_query_from", "hsp_query_to", "hsp_hit_from", "hsp_hit_to",
                       "hsp_align_len", "hsp_expect", "hsp_identity", "hsp_score")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read(payload, source="ebi_job_dispatcher"):
    """EBI BLAST JSON -> [hit, ...] ordered as the service returned them.

    `payload` is the decoded JSON document, so the caller decides where the bytes came
    from -- a live job or a cached raw response replayed months later.
    """
    if not isinstance(payload, dict):
        raise InputValidationError("%s result is not a JSON object" % source)
    hits_in = payload.get("hits")
    if hits_in is None:
        raise InputValidationError(
            "%s result has no 'hits' key; this is not a BLAST JSON result" % source)

    out = []
    for hit in hits_in:
        hsps = hit.get("hit_hsps") or []
        if not hsps:
            continue
        # best HSP by e-value; ties keep the service's own ordering
        best = min(hsps, key=lambda h: (_num(h.get("hsp_expect")) is None,
                                        _num(h.get("hsp_expect"))))
        missing = [f for f in REQUIRED_HSP_FIELDS if best.get(f) in (None, "")]
        if missing:
            # The gate, enforced at parse time so no downstream stage can quietly
            # build a full-length record from a coordinate-free response.
            raise InputValidationError(
                "%s result lacks alignment coordinates (%s). RBPfinder requires a "
                "coordinate-bearing format; the summary table is not accepted."
                % (source, ", ".join(missing)))

        q_from, q_to = int(best["hsp_query_from"]), int(best["hsp_query_to"])
        align_len = int(best["hsp_align_len"])
        identity = _num(best.get("hsp_identity"))
        qlen = _num(payload.get("query_len"))
        desc = (hit.get("hit_desc") or hit.get("hit_def") or "").strip()
        acc = accession.subject_identity(
            hit.get("hit_acc") or hit.get("hit_id") or "")
        out.append({
            "rank": len(out) + 1,
            "accession": acc,
            "database": hit.get("hit_db") or "UniProtKB",
            # strip the accession the service repeats inside the description, so the
            # term classifier sees the words and not the identifier
            "description": desc[len(acc):].strip() if desc.startswith(acc) else desc,
            "description_full": desc,
            "organism": hit.get("hit_os") or "",
            "identity_pct": identity,
            "evalue": _num(best.get("hsp_expect")),
            "score": _num(best.get("hsp_score")),
            "coverage_pct": (round(100.0 * (q_to - q_from + 1) / qlen, 1)
                             if qlen else None),
            "query_span": (q_from, q_to),
            "target_span": (int(best["hsp_hit_from"]), int(best["hsp_hit_to"])),
            "alignment_length": align_len,
        })
    return out


def database_release(payload):
    """What the service says its database was. Never invent one.

    An absent release is recorded as the string `unknown`, not as an empty field: an
    empty field reads as "not recorded", which then reads as "not searched", and this
    project has already paid for that confusion twice.
    """
    for key in ("dbrelease", "db_release", "database_release"):
        v = payload.get(key)
        if v:
            return str(v)
    dbs = payload.get("dbs") or []
    if dbs and isinstance(dbs, list) and isinstance(dbs[0], dict):
        # ONLY a release field. Falling back to the database NAME was a real defect
        # caught by the M16-b provenance gate: it made "uniprotkb" look like a version,
        # which is worse than `unknown` -- a plausible-looking answer stops anyone
        # asking, while `unknown` says plainly that the service did not tell us.
        rel = dbs[0].get("dbrelease") or dbs[0].get("release")
        if rel:
            return str(rel)
    return "unknown"
