"""local_blast_db provider -- runs blastp against a database on this machine.

Registered in data_policy.provider_registry with `transmits: false`, so it is
permitted in `private` mode and refused in `import_only`, which parses existing
results and runs no search at all.

Results are cached by (database, query sha256) so a rerun does not re-BLAST, and
so a benchmark result stays reproducible.
"""
import hashlib
import json
import pathlib
import subprocess

from ..parse import blast_tabular
from ..capability import BlastCapability
from ..errors import CapabilityUnavailable

PROVIDER_ID = "local_blast_db"

# kept as a name for existing callers; it is the shared capability error now, so every
# provider fails the same way instead of inventing its own exception type
BlastUnavailable = CapabilityUnavailable


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class LocalBlast:
    def __init__(self, blastp_exe=None, db_path=None, cache_dir=None, evalue=1e-3,
                 max_target_seqs=10, threads=4, capability=None):
        # Searching an existing database needs blastp and nothing else -- makeblastdb
        # may be absent and Stage 2 still works. That is why callers declare their own
        # requirements rather than asking whether "BLAST" is available.
        cap = capability or BlastCapability(blastp=blastp_exe)
        self.capability = cap
        self.exe = pathlib.Path(cap.require("blastp")["blastp"])
        self.db = str(db_path)
        self.evalue = evalue
        self.max_target_seqs = max_target_seqs
        self.threads = threads
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- metadata ----------------------------------------------------------
    def database_metadata(self):
        meta = pathlib.Path(self.db + ".metadata.yaml")
        return meta.read_text(encoding="utf-8") if meta.exists() else None

    # -- search ------------------------------------------------------------
    def _cache_path(self, key):
        return self.cache_dir / ("%s.tsv" % key) if self.cache_dir else None

    def search_many(self, records):
        """BLAST a batch of CDS records at once. Returns {cds_id: [hit, ...]}.

        One blastp invocation for the whole pool is far cheaper than one per
        protein, and BLAST reports per-query anyway.
        """
        todo, cached, out = [], {}, {}
        for rec in records:
            seq = rec.get("translation")
            if not seq:
                continue
            key = _sha(self.db + "|" + seq)
            cp = self._cache_path(key)
            if cp and cp.exists():
                cached[rec["cds_id"]] = cp
            else:
                todo.append((rec, key))

        for cid, cp in cached.items():
            out[cid] = blast_tabular.read(cp).get(cid, [])

        if todo:
            import tempfile
            with tempfile.TemporaryDirectory() as td:
                td = pathlib.Path(td)
                qf, of = td / "q.faa", td / "hits.tsv"
                with qf.open("w", encoding="utf-8", newline="\n") as fh:
                    for rec, _ in todo:
                        fh.write(">%s\n%s\n" % (rec["cds_id"], rec["translation"]))
                cmd = [str(self.exe), "-query", str(qf), "-db", self.db,
                       "-out", str(of), "-outfmt", blast_tabular.OUTFMT,
                       "-evalue", str(self.evalue),
                       "-max_target_seqs", str(self.max_target_seqs),
                       "-num_threads", str(self.threads)]
                proc = subprocess.run(cmd, capture_output=True, text=True)
                if proc.returncode != 0:
                    raise BlastUnavailable(
                        "blastp failed (%d): %s" % (proc.returncode, proc.stderr[:400]))
                parsed = blast_tabular.read(of)
                for rec, key in todo:
                    hits = parsed.get(rec["cds_id"], [])
                    out[rec["cds_id"]] = hits
                    cp = self._cache_path(key)
                    if cp:
                        # cache even an empty result: "searched and found nothing"
                        # is a different state from "never searched"
                        with cp.open("w", encoding="utf-8", newline="\n") as fh:
                            for h in hits:
                                fh.write("\t".join([
                                    rec["cds_id"], h["accession"],
                                    str(h["identity_pct"]), str(h["alignment_length"]),
                                    str(h["query_span"][0]), str(h["query_span"][1]),
                                    str(h["target_span"][0]), str(h["target_span"][1]),
                                    str(h["evalue"]), str(h["score"]),
                                    str(h["coverage_pct"]), h["description_full"],
                                ]) + "\n")
        return out


FETCHER_V2 = {
    "script": "scripts/fetch_uniprot.ps1",
    "protocol_version": 2,
    "max_time_seconds": 45,
    "transport_retry_owner": "outer_loop",
    "incremental_last_accession": True,
    "page_size": 500,
    "delay_seconds": 3,
}


def write_database_metadata(db_path, **fields):
    """Freeze what a benchmark result was produced against.

    Without this, a complete-recall number that moves six months from now cannot
    be attributed: rule change or database update?
    """
    p = pathlib.Path(str(db_path) + ".metadata.yaml")
    lines = ["# frozen database provenance -- do not edit by hand"]
    for k in ("database_name", "source", "release", "download_date", "fasta_sha256",
              "blast_db_build_command", "blast_version", "sequence_count"):
        lines.append("%s: %s" % (k, json.dumps(fields.get(k, ""), ensure_ascii=False)))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p
