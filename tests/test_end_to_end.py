"""One offline Mode A run on a synthetic pharokka-style GenBank.

Not a benchmark -- the proteins are random and prove nothing biological. It pins that a
plain install can take an annotated GenBank to a schema-valid result with no network,
no BLAST and no database, and that product-name traps do not reach the shortlist.
"""
import json
import random

from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation, SeqFeature
from Bio.SeqRecord import SeqRecord
from Bio import SeqIO

from rbpfinder import cli

PRODUCTS = [
    ("terminase large subunit", "head and packaging"),
    ("portal protein", "head and packaging"),
    ("major head protein", "head and packaging"),
    ("head-tail adaptor", "connector"),
    ("major tail protein", "tail"),
    ("tail length tape measure protein", "tail"),
    ("distal tail protein Dit", "tail"),
    ("baseplate protein", "tail"),
    ("tail fiber protein", "tail"),
    ("tail fiber assembly protein", "tail"),
    ("hypothetical protein", "unknown function"),
    ("holin", "lysis"),
    ("endolysin", "lysis"),
    ("DNA polymerase", "DNA, RNA and nucleotide metabolism"),
    ("metallopeptidase", "other"),
    ("catalytic subunit", "other"),
    ("RNA editing protein", "other"),
    ("DNA helicase", "DNA, RNA and nucleotide metabolism"),
]


def _genbank(path):
    rng = random.Random(7)
    aa = "ACDEFGHIKLMNPQRSTVWY"
    genome, feats = "", []
    for i, (product, function) in enumerate(PRODUCTS, 1):
        prot = "M" + "".join(rng.choice(aa) for _ in range(299))
        nt = "".join({"M": "ATG"}.get(a, None) or _codon(a) for a in prot) + "TAA"
        start = len(genome)
        genome += nt + "ACGT" * 5
        feats.append(SeqFeature(
            FeatureLocation(start, start + len(nt), strand=1), type="CDS",
            qualifiers={"locus_tag": ["SYN_CDS_%04d" % i], "product": [product],
                        "function": [function], "phrog": ["%d" % (1000 + i)],
                        "translation": [prot]}))
    rec = SeqRecord(Seq(genome), id="SYN1", name="SYN1", description="synthetic phage",
                    annotations={"molecule_type": "DNA", "topology": "linear"})
    rec.features = feats
    SeqIO.write(rec, str(path), "genbank")


_CODONS = dict(zip("ACDEFGHIKLNPQRSTVWY",
                   "GCT TGT GAT GAA TTT GGT CAT ATT AAA CTG AAT CCG CAG CGT TCT ACC "
                   "GTT TGG TAT".split()))


def _codon(a):
    return _CODONS[a]


def test_offline_mode_a_run(tmp_path):
    gbk = tmp_path / "syn.gbk"
    _genbank(gbk)
    out = tmp_path / "run"
    code = cli.main(["--genbank", str(gbk), "--phage-id", "SYN", "--out", str(out),
                     "--no-local-stage2", "--quiet-progress"])
    assert code in (0, None)

    result = json.loads((out / "run_result.json").read_text(encoding="utf-8"))
    assert result

    def table(name):
        lines = (out / name).read_text(encoding="utf-8").splitlines()
        header = lines[0].split("\t")
        return {row["cds_id"]: row
                for row in (dict(zip(header, ln.split("\t"))) for ln in lines[1:])}

    screening = table("all_CDS_screening.tsv")
    assert len(screening) == len(PRODUCTS)

    # "catalytic subunit" / "RNA editing protein" used to be STRONG tail anchors
    # (substring `Tal` / `Dit`) and opened a C1 window over the replication module.
    for trap in ("SYN_CDS_0015", "SYN_CDS_0016", "SYN_CDS_0017"):
        assert "C1" not in screening[trap]["nomination_channels"].split(",")
    # ... and the portal was nominated by C2 through the same substring.
    assert "C2" not in screening["SYN_CDS_0002"]["nomination_channels"].split(",")
    assert "C1" in screening["SYN_CDS_0009"]["nomination_channels"].split(",")

    candidates = table("RBP_candidates.tsv")
    primary = {c for c, row in candidates.items()
               if "primary" in row["set_membership"].lower()}
    # The fibre is the shortlist; its assembly chaperone is not.
    assert primary == {"SYN_CDS_0009"}
    assert candidates["SYN_CDS_0010"]["tier"].startswith("R3")
