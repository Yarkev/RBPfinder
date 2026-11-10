"""`rbpfinder validate-input` -- can this annotation actually be analysed?

Answered in seconds, before anything expensive happens. The standardised server-side
pipeline hands annotation to an orchestrator (Biomni calls pharokka, and phold when the
user asked for the accurate route), and phold on CPU is an hour or more. Discovering only
afterwards that the GenBank has no `/translation`, or locus tags RBPfinder cannot key on,
wastes that hour. So the precondition is checkable on its own.

It reports; it changes nothing, downloads nothing and transmits nothing. It uses the SAME
readers the run uses -- `ingest.genbank` and `ingest.phold` -- so a file this command
accepts is a file the run accepts, and there is no second opinion to drift.

    rbpfinder validate-input --genbank pharokka.gbk
    rbpfinder validate-input --genbank phold.gbk --json
    rbpfinder validate-input --phold phold_per_cds_predictions.tsv --json

Exit 0 when the file is usable, 2 when it is not (the ordinary input-error code).
"""
import argparse
import json
import sys

from .errors import EXIT_OK, EXIT_INPUT
from .ingest import genbank as genbank_in
from .ingest import phold as phold_in


def inspect_genbank(path, phage_id="VALIDATE"):
    """What the authoritative reader makes of this GenBank."""
    out = {"input": str(path), "kind": "genbank", "usable": False, "problems": [],
           "warnings": [], "cds_count": 0, "with_translation": 0}
    try:
        cds, meta = genbank_in.read(path, phage_id=phage_id)
    except genbank_in.GenBankIngestError as exc:
        out["problems"].append(str(exc))
        return out
    except Exception as exc:                                  # noqa: BLE001
        out["problems"].append("could not parse as GenBank: %s" % exc)
        return out

    out["cds_count"] = len(cds)
    out["with_translation"] = sum(
        1 for r in cds if r.get("translation_source") == "genbank")
    out["warnings"] = list(meta.get("warnings") or [])[:20]
    out["gene_caller"] = meta.get("gene_caller")
    out["genome_length_nt"] = meta.get("genome_length")
    out["contigs"] = meta.get("contigs")

    if not cds:
        out["problems"].append(
            "no CDS features: this looks like a bare nucleotide record. RBPfinder does "
            "not annotate genomes -- annotate it first (pharokka), then validate again")
        return out
    if out["with_translation"] == 0:
        out["problems"].append(
            "no CDS carries a /translation. Protein sequence is required for sequence "
            "homology and for local structure search; re-run the annotator so it writes "
            "translations")
        return out
    if out["with_translation"] < out["cds_count"]:
        out["warnings"].append(
            "%d of %d CDS have no /translation; those cannot be searched"
            % (out["cds_count"] - out["with_translation"], out["cds_count"]))

    out["usable"] = True
    out["mode"] = "A_annotated"
    out["supports_local_structure_search"] = True
    return out


def inspect_phold_tsv(path):
    """What the phold per-CDS reader makes of this table.

    The one thing worth saying loudly: this table carries no protein sequence. It is a
    complete input for annotation-derived evidence, and it cannot drive local structure
    search or any sequence batch. For the accurate route, hand over phold's GenBank
    instead -- it has phold's products AND the translations.
    """
    out = {"input": str(path), "kind": "phold_tsv", "usable": False, "problems": [],
           "warnings": [], "cds_count": 0, "with_translation": 0}
    try:
        cds = phold_in.read(path)
    except KeyError as exc:
        out["problems"].append("phold table is missing a required column: %s" % exc)
        return out
    except Exception as exc:                                  # noqa: BLE001
        out["problems"].append("could not parse as a phold per-CDS table: %s" % exc)
        return out

    out["cds_count"] = len(cds)
    if not cds:
        out["problems"].append("the phold table has no rows")
        return out

    out["usable"] = True
    out["mode"] = "B_phold_table"
    out["supports_local_structure_search"] = False
    out["warnings"].append(
        "a phold table carries no protein sequence, so local Foldseek structure search "
        "and every sequence-carrying batch are unavailable from this input. For the "
        "accurate route prefer phold's GenBank (--genbank phold.gbk): it has phold's "
        "improved products and the translations")
    return out


def render(info):
    lines = []
    lines.append("input      %s" % info["input"])
    lines.append("kind       %s" % info["kind"])
    lines.append("CDS        %d (%d with /translation)"
                 % (info["cds_count"], info["with_translation"]))
    if info.get("genome_length_nt"):
        lines.append("genome     %s nt%s"
                     % (info["genome_length_nt"],
                        (", %s contig(s)" % info["contigs"]) if info.get("contigs")
                        else ""))
    if info.get("gene_caller"):
        lines.append("caller     %s" % info["gene_caller"])
    lines.append("structure  local Foldseek %s from this input"
                 % ("CAN run" if info.get("supports_local_structure_search")
                    else "CANNOT run"))
    for w in info.get("warnings") or []:
        lines.append("  warning  %s" % w)
    for prob in info.get("problems") or []:
        lines.append("  PROBLEM  %s" % prob)
    lines.append("")
    lines.append("VALIDATE_INPUT=%s" % ("USABLE" if info["usable"] else "NOT_USABLE"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="rbpfinder validate-input",
        description="check that an annotation can be analysed; reports only")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--genbank", help="an annotated GenBank (pharokka or phold output)")
    src.add_argument("--phold", help="a phold per-CDS predictions table")
    ap.add_argument("--phage-id", default="VALIDATE",
                    help="only affects the CDS ids in this report")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable output for an orchestrator")
    args = ap.parse_args(argv)

    if args.genbank:
        info = inspect_genbank(args.genbank, phage_id=args.phage_id)
    else:
        info = inspect_phold_tsv(args.phold)

    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=1, default=str))
    else:
        print(render(info))
    return EXIT_OK if info["usable"] else EXIT_INPUT


if __name__ == "__main__":                                    # pragma: no cover
    sys.exit(main())
