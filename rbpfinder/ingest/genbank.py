"""Mode A input: a pharokka-annotated GenBank file.

This parser answers exactly one question -- "what does the GenBank say?" -- and
never "what does that mean?". No module assignment, no evidence direction, no
role: those stay in decision_rules.yaml and are applied by Stage 0/1. Letting
them leak in here would smuggle biological judgement back into Python.

Output is the same normalised CDS record shape that ingest/phold.py produces, so
everything downstream is agnostic to which input path was used.
"""
import pathlib
import re

from Bio import SeqIO

# Only an explicit trailing CDS number is accepted. Position in the file is
# never used as a fallback: guessing an id would silently break the traceability
# that lets 114/114 reference RBPs map to a locus tag.
_CDS_NUM = re.compile(r"CDS_?(\d+)$")


class GenBankIngestError(Exception):
    """A defect that makes the input untrustworthy as benchmark ground truth."""


def _canonical(phage_id, num):
    return "%s_CDS_%04d" % (phage_id, num)


def _one(q, key, tag, path_name, warnings):
    """Promote a qualifier to a single scalar.

    A GenBank qualifier may legally repeat. Anything promoted to a typed record
    field must be scalar, so the first value wins and the repeat is reported --
    the full list survives untouched in qualifiers_raw.
    """
    v = q.get(key)
    if v is None:
        return None
    if isinstance(v, list):
        if len(v) > 1:
            warnings.append(
                "%s: %s has %d /%s qualifiers %r; using the first"
                % (path_name, tag, len(v), key, v))
        return str(v[0]) if v else None
    return str(v)


def _joined(q, key):
    """Reassemble a value that pharokka split across repeated qualifiers.

    pharokka serialises a comma-containing value by emitting one qualifier per
    comma-separated part, so

        /function="DNA"
        /function=" RNA and nucleotide metabolism"

    is ONE value, not two. Taking the first part would yield "DNA", which matches
    no module_map key and would silently push every replication CDS in all 48
    genomes into the `unknown` module. This is a serialisation detail, so fixing
    it belongs here; what the reassembled string MEANS is still decided by YAML.
    """
    v = q.get(key)
    if v is None:
        return None
    if isinstance(v, list):
        return ", ".join(str(x).strip() for x in v if str(x).strip())
    return str(v).strip()


def _location_parts(feat):
    loc = feat.location
    parts = getattr(loc, "parts", None) or [loc]
    return [(int(p.start) + 1, int(p.end)) for p in parts]


def read(path, phage_id=None, is_circular=None):
    """Parse one pharokka GenBank. Returns (records, meta)."""
    path = pathlib.Path(path)
    phage_id = phage_id or path.stem

    seq_records = list(SeqIO.parse(str(path), "genbank"))
    if not seq_records:
        raise GenBankIngestError("%s: no GenBank records" % path.name)

    records, warnings, seen_nums = [], [], {}
    genome_length = sum(len(sr.seq) for sr in seq_records)

    for sr in seq_records:
        contig_len = len(sr.seq)
        topology = (sr.annotations or {}).get("topology", "linear")
        circular = is_circular if is_circular is not None else (topology == "circular")

        for feat in sr.features:
            if feat.type != "CDS":
                continue
            q = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v)
                 for k, v in feat.qualifiers.items()}
            _at = str(feat.location)

            # --- check 3: no locus tag, or an id we cannot parse -> hard failure
            tag = (_one(q, "locus_tag", _at, path.name, warnings)
                   or _one(q, "ID", _at, path.name, warnings))
            if not tag:
                raise GenBankIngestError(
                    "%s: a CDS at %s has neither /locus_tag nor /ID; refusing to "
                    "invent an identifier" % (path.name, feat.location))
            m = _CDS_NUM.search(str(tag))
            if not m:
                raise GenBankIngestError(
                    "%s: locus tag %r does not end in CDS_<number>; refusing to fall "
                    "back to file order" % (path.name, tag))
            num = int(m.group(1))

            # --- check 2: two CDS claiming the same number -> hard failure
            if num in seen_nums:
                raise GenBankIngestError(
                    "%s: duplicate CDS number %04d (%s and %s)"
                    % (path.name, num, seen_nums[num], tag))
            seen_nums[num] = str(tag)

            # --- check 4: compound locations and origin spanning are kept, not flattened
            parts = _location_parts(feat)
            lo, hi = min(p[0] for p in parts), max(p[1] for p in parts)
            spans_origin = bool(
                len(parts) > 1 and circular and lo == 1 and hi == contig_len
            )
            if len(parts) > 1:
                warnings.append(
                    "%s: %s has a compound location %s; start_nt/end_nt are the outer "
                    "extent and ordering for this CDS is approximate%s"
                    % (path.name, tag, parts,
                       " (spans origin)" if spans_origin else ""))

            # --- check 1: use the stored translation, never silently re-translate
            translation = _one(q, "translation", tag, path.name, warnings)
            if translation:
                length_aa = len(translation)
                translation_source = "genbank"
                expected = (hi - lo + 1) // 3 - 1
                if abs(length_aa - expected) > 1:
                    warnings.append(
                        "%s: %s /translation is %d aa but its span implies ~%d aa"
                        % (path.name, tag, length_aa, expected))
            else:
                length_aa = max((hi - lo + 1) // 3 - 1, 1)
                translation_source = "absent_derived_from_span"
                warnings.append(
                    "%s: %s has no /translation; length taken from coordinates"
                    % (path.name, tag))

            phrog = _one(q, "phrog", tag, path.name, warnings)
            phrog = None if phrog in (None, "", "No_PHROG") else phrog

            records.append({
                # identity -- the raw tag carries pharokka's random prefix and is kept
                # for traceability, but nothing downstream may key on it.
                "cds_id": _canonical(phage_id, num),
                "raw_locus_tag": str(tag),
                "cds_number": num,
                "contig_id": sr.id,
                # coordinates
                "start_nt": lo,
                "end_nt": hi,
                "strand": "-" if feat.location.strand == -1 else "+",
                "location_parts": parts,
                "spans_origin": spans_origin,
                # sequence
                "translation": translation,
                "translation_source": translation_source,
                "length_aa": length_aa,
                # annotation, verbatim
                "phrog": phrog,
                "function": _joined(q, "function") or "unknown function",
                "product": _joined(q, "product") or "",
                "annotation_method": "pharokka" if phrog else "none",
                "annotation_source": "pharokka" if phrog else "none",
                "annotation_confidence": "pharokka" if phrog else "none",
                # --- check 5: every qualifier survives, including ones we do not use
                "qualifiers_raw": q,
                # phold-only fields, absent on this path. Their absence is what makes
                # the run flag STRUCTURAL_SCREEN_UNAVAILABLE; it is never read as a
                # negative result.
                "bitscore": None, "fident": None, "evalue": None,
                "qstart": None, "qend": None, "qlen": None, "qcov": None,
                "tophit_protein": None, "prostt5_confidence": None,
                "category_proportions": {},
            })

    records.sort(key=lambda x: (x["start_nt"], x["cds_id"]))
    meta = {
        "phage_id": phage_id,
        "source_file": path.name,
        "input_mode": "A_annotated",
        "gene_caller": "phanotate",
        "n_cds": len(records),
        "genome_length": genome_length,
        "is_circular": bool(is_circular) if is_circular is not None else False,
        "structural_screen": "STRUCTURAL_SCREEN_UNAVAILABLE",
        "warnings": warnings,
    }
    return records, meta
