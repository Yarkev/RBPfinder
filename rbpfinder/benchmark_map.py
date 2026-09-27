"""Map an adjudicated reference protein onto a called CDS BY SEQUENCE.

Product text is not a mapping axis. On T4 it silently mapped gp37 (long tail
fibre) and gp12 (short tail fibre) onto the SAME CDS and reported both as high
confidence, because pharokka calls both "tail fiber protein". A collision
reported as certainty is worse than an honest ambiguity, and it would have
carried one protein's identity and coverage into the other's leakage grade.

Sequence identity is the correct axis: the reference entry and the called CDS
are the same protein from the same genome, so a true match is near-exact.
"""
import pathlib
import re

from .parse import accession
import subprocess
import tempfile


def load_reference_sequences(fasta_paths, accessions):
    """Pull reference proteins out of one or more local FASTA files. No network.

    Handles both header styles in use here: Swiss-Prot `>sp|P03749|TIPJ_LAMBD ...`
    and plain RefSeq `>YP_950543.1 description`.
    """
    if isinstance(fasta_paths, (str, pathlib.Path)):
        fasta_paths = [fasta_paths]
    want = {str(a).upper() for a in accessions}
    out = {}
    for fp in fasta_paths:
        fp = pathlib.Path(fp)
        if not fp.exists():
            continue
        acc, buf = None, []
        for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(">"):
                if acc and acc in want:
                    out[acc] = "".join(buf)
                head = line[1:].split()[0]
                acc = accession.subject_identity(head).upper()
                buf = []
            else:
                buf.append(line.strip())
        if acc and acc in want:
            out[acc] = "".join(buf)
    return out


def map_by_sequence(ref_seqs, cds_records, blastp, thresholds=None):
    """Align each reference against this genome's CDS proteins.

    Returns {ref_key: {cds_id, identity_pct, query_cov, confidence, note}}.
    """
    t = thresholds or {"high_identity": 95.0, "high_cov": 0.90,
                       "medium_identity": 70.0, "medium_cov": 0.60}
    have = [r for r in cds_records if r.get("translation")]
    if not have or not ref_seqs:
        return {k: {"cds_id": None, "confidence": "unmapped",
                    "note": "no translations available"} for k in ref_seqs}

    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        db = tmp / "cds.faa"
        db.write_text("".join(">%s\n%s\n" % (r["cds_id"], r["translation"])
                              for r in have), encoding="utf-8")
        q = tmp / "ref.faa"
        q.write_text("".join(">%s\n%s\n" % (k, v) for k, v in ref_seqs.items()),
                     encoding="utf-8")
        subprocess.run([str(pathlib.Path(blastp).with_name("makeblastdb.exe")),
                        "-in", str(db), "-dbtype", "prot", "-out", str(tmp / "d")],
                       capture_output=True, text=True)
        p = subprocess.run(
            [str(blastp), "-query", str(q), "-db", str(tmp / "d"),
             "-outfmt", "6 qseqid sseqid pident length qstart qend qlen evalue bitscore",
             "-max_target_seqs", "5", "-evalue", "1e-5"],
            capture_output=True, text=True)

    best = {}
    for line in p.stdout.splitlines():
        f = line.split("\t")
        if len(f) < 9:
            continue
        qid, sid, pident, alen, qs, qe, qlen = f[0], f[1], float(f[2]), int(f[3]), \
            int(f[4]), int(f[5]), int(f[6])
        cov = (qe - qs + 1) / float(qlen) if qlen else 0.0
        score = float(f[8])
        if qid not in best or score > best[qid]["_score"]:
            best[qid] = {"cds_id": sid, "identity_pct": round(pident, 1),
                         "query_cov": round(cov, 3), "_score": score}

    out = {}
    for key in ref_seqs:
        b = best.get(key)
        if not b:
            out[key] = {"cds_id": None, "identity_pct": None, "query_cov": None,
                        "confidence": "unmapped",
                        "note": "no blastp hit against this genome's CDS"}
            continue
        ident, cov = b["identity_pct"], b["query_cov"]
        if ident >= t["high_identity"] and cov >= t["high_cov"]:
            conf, note = "high", "near-identical: %.1f%% over %.0f%% of the reference" % (
                ident, cov * 100)
        elif ident >= t["medium_identity"] and cov >= t["medium_cov"]:
            conf, note = "medium", "%.1f%% identity over %.0f%% -- same family, strain differs" % (
                ident, cov * 100)
        else:
            conf, note = "low_ambiguous", "weak: %.1f%% identity over %.0f%%" % (
                ident, cov * 100)
        out[key] = {"cds_id": b["cds_id"], "identity_pct": ident,
                    "query_cov": cov, "confidence": conf, "note": note}

    # A CDS claimed by two references is a collision, not two high-confidence
    # mappings. Keep the stronger one and demote the other rather than letting
    # both stand -- this is precisely the T4 gp37/gp12 failure.
    claimed = {}
    for key, m in out.items():
        if not m.get("cds_id"):
            continue
        claimed.setdefault(m["cds_id"], []).append(key)
    for cds_id, keys in claimed.items():
        if len(keys) < 2:
            continue
        keys.sort(key=lambda k: -(out[k]["identity_pct"] or 0))
        for loser in keys[1:]:
            out[loser]["confidence"] = "low_ambiguous"
            out[loser]["note"] = ("collides with %s on %s; both cannot be the same CDS"
                                  % (keys[0], cds_id.rsplit("_", 1)[-1]))
    return out


def detect_fragmentation(ref_seqs, cds_records, blastp, min_combined_cov=0.85,
                         max_cds_gap=3, max_span_overlap=0.15):
    """Flag a reference protein whose alignment is split across adjacent CDS.

    Fires when ONE reference matches TWO nearby, same-strand CDS over
    complementary, largely non-overlapping stretches whose combined coverage is
    high. Found on lambda stf: residues 1-395 to one CDS and 461-774 to the next.

    It is a WARNING and nothing more. It never merges the CDS and never calls the
    result an RBP, because the two readings are genuinely different biology:

      * a gene caller split one real protein, or
      * the genome truly carries two proteins because of a frameshift, which is
        what lambda PaPa does to stf.

    Only the sequence can tell those apart, so the decision stays with a human.
    """
    import subprocess as _sp
    import tempfile as _tf

    have = [r for r in cds_records if r.get("translation")]
    if not have or not ref_seqs:
        return []

    with _tf.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "db.faa").write_text(
            "".join(">%s\n%s\n" % (r["cds_id"], r["translation"]) for r in have),
            encoding="utf-8")
        (tmp / "q.faa").write_text(
            "".join(">%s\n%s\n" % (k, v) for k, v in ref_seqs.items()), encoding="utf-8")
        _sp.run([str(pathlib.Path(blastp).with_name("makeblastdb.exe")),
                 "-in", str(tmp / "db.faa"), "-dbtype", "prot", "-out", str(tmp / "d")],
                capture_output=True, text=True)
        p = _sp.run([str(blastp), "-query", str(tmp / "q.faa"), "-db", str(tmp / "d"),
                     "-outfmt", "6 qseqid sseqid pident qstart qend qlen evalue",
                     "-max_target_seqs", "10", "-evalue", "1e-5"],
                    capture_output=True, text=True)

    hits = {}
    for line in p.stdout.splitlines():
        f = line.split("\t")
        if len(f) < 7:
            continue
        hits.setdefault(f[0], []).append(
            {"cds_id": f[1], "pident": float(f[2]), "qstart": int(f[3]),
             "qend": int(f[4]), "qlen": int(f[5])})

    idx = {r["cds_id"]: i for i, r in enumerate(cds_records)}
    strand = {r["cds_id"]: r.get("strand") for r in cds_records}
    out = []
    for ref, hs in hits.items():
        hs.sort(key=lambda h: -(h["qend"] - h["qstart"]))
        for i in range(len(hs)):
            for j in range(i + 1, len(hs)):
                a, b = hs[i], hs[j]
                if a["cds_id"] == b["cds_id"]:
                    continue
                ia, ib = idx.get(a["cds_id"]), idx.get(b["cds_id"])
                if ia is None or ib is None or abs(ia - ib) > max_cds_gap:
                    continue
                if strand.get(a["cds_id"]) != strand.get(b["cds_id"]):
                    continue
                lo, hi = (a, b) if a["qstart"] <= b["qstart"] else (b, a)
                overlap = max(0, lo["qend"] - hi["qstart"] + 1)
                span = (hi["qend"] - lo["qstart"] + 1)
                if span <= 0 or overlap / float(span) > max_span_overlap:
                    continue
                combined = ((lo["qend"] - lo["qstart"] + 1) +
                            (hi["qend"] - hi["qstart"] + 1)) / float(a["qlen"])
                if combined < min_combined_cov:
                    continue
                out.append({
                    "flag": "POTENTIAL_GENE_FRAGMENTATION",
                    "reference": ref,
                    "cds_a": lo["cds_id"], "span_a": (lo["qstart"], lo["qend"]),
                    "cds_b": hi["cds_id"], "span_b": (hi["qstart"], hi["qend"]),
                    "cds_distance": abs(ia - ib),
                    "combined_query_coverage": round(combined, 3),
                    "identity_a": lo["pident"], "identity_b": hi["pident"],
                    "action": "human review only; never auto-merge, never auto-call RBP",
                    "alternatives": ["gene caller split one protein",
                                     "the strain genuinely carries a frameshift"],
                })
                break
    return out
