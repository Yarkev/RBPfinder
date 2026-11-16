"""Mode A input: an annotated GenBank file.

pharokka output is the reference shape, but any annotated GenBank or RefSeq record is
accepted. The hard requirements are CDS features carrying /translation and some form of
/locus_tag or /ID.

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

from ..errors import InputValidationError

# An explicit trailing CDS number is PREFERRED: it maps 1:1 onto pharokka output and
# keeps the id stable. A tag in any other shape (NCBI RefSeq's `T7p01`, ...) is not a
# defect -- the raw tag is preserved verbatim in `raw_locus_tag` and a number is assigned
# in GENOME COORDINATE order, with a warning naming the deviation.
#
# Coordinate order, not file order: a GenBank whose features are emitted out of order --
# which happens -- would otherwise produce different ids for the same proteins on a
# re-export, and `cds_id` is the key that `all_CDS_screening.tsv`, every manifest, the
# search state and `--stage2-manifest` replay are all written against. Coordinate order
# is a property of the genome; file order is a property of the file.
#
# What this still cannot promise: ids derived this way change if the genome is
# RE-ANNOTATED with a different caller, because the CDS set itself changes. Artefacts
# must not be mixed across annotation sources -- see USER_GUIDE.
_CDS_NUM = re.compile(r"CDS_?(\d+)$")


class GenBankIngestError(InputValidationError):
    """A defect that makes the input untrustworthy as benchmark ground truth.

    An InputValidationError, so it carries exit code 2 and is caught by the same handler
    every other input problem uses. It used to derive from bare Exception, which made it
    the "third shape" this package's errors module exists to prevent: it escaped as a raw
    traceback with exit 1, telling an agent that a malformed input file was an unexpected
    internal error.
    """


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

    # --- pass 1: which CDS carry an explicit CDS_<n>, and what number does each CDS
    # without one get? Numbers are assigned in genome coordinate order and start above
    # the highest explicit number, so the two schemes can coexist in one file without
    # ever colliding. Duplicate EXPLICIT numbers stay a hard failure.
    tagless = []
    for si, sr in enumerate(seq_records):
        for fi, feat in enumerate(sr.features):
            if feat.type != "CDS":
                continue
            q = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v)
                 for k, v in feat.qualifiers.items()}
            tag = (q.get("locus_tag") or q.get("ID"))
            if isinstance(tag, list):
                tag = tag[0] if tag else None
            if not tag:
                # No identifier at all stays a hard failure: there would be nothing to
                # preserve in raw_locus_tag, so traceability would be lost, not delegated.
                raise GenBankIngestError(
                    "%s: a CDS at %s has neither /locus_tag nor /ID; refusing to "
                    "invent an identifier" % (path.name, feat.location))
            m = _CDS_NUM.search(str(tag))
            if m:
                num = int(m.group(1))
                if num in seen_nums:
                    raise GenBankIngestError(
                        "%s: duplicate CDS number %04d (%s and %s)"
                        % (path.name, num, seen_nums[num], tag))
                seen_nums[num] = str(tag)
            else:
                parts = _location_parts(feat)
                tagless.append((min(p[0] for p in parts), si, fi, str(tag)))

    assigned = {}
    next_num = max(seen_nums, default=0) + 1
    for _lo, si, fi, tag in sorted(tagless):
        assigned[(si, fi)] = next_num
        warnings.append(
            "%s: locus tag %r does not end in CDS_<number>; assigned CDS_%04d by genome "
            "coordinate order (the raw tag is preserved in raw_locus_tag)"
            % (path.name, tag, next_num))
        next_num += 1

    resolved_circular = False
    for si, sr in enumerate(seq_records):
        contig_len = len(sr.seq)
        topology = (sr.annotations or {}).get("topology", "linear")
        circular = is_circular if is_circular is not None else (topology == "circular")
        resolved_circular = resolved_circular or bool(circular)

        for fi, feat in enumerate(sr.features):
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
            # Pass 1 settled both the explicit numbers and the assigned ones, including
            # every duplicate check; this pass only reads the answer.
            num = int(m.group(1)) if m else assigned[(si, fi)]

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
        # pharokka calls genes with PHANOTATE; any other annotator did not, and saying
        # it did puts a false provenance in the run record.
        "gene_caller": ("phanotate"
                        if any(r.get("annotation_source") == "pharokka"
                               for r in records) else "genbank_annotator"),
        "n_cds": len(records),
        "genome_length": genome_length,
        # The RESOLVED circularity, not the raw flag: a record that declares
        # `topology: circular` is circular even when no --circular was passed. C1's
        # origin handling keys off this, and reporting False here left that fix dormant
        # on exactly the records it was written for.
        "is_circular": resolved_circular,
        "structural_screen": "STRUCTURAL_SCREEN_UNAVAILABLE",
        "warnings": warnings,
    }
    return records, meta
