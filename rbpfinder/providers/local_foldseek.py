"""local_foldseek provider -- runs Foldseek on this machine, structure search from
amino-acid sequence via ProstT5.

Registered in data_policy.provider_registry as `local_foldseek`
(family T, transmits: false, kind: local_search), so it is permitted in `private` and
`remote_allowed` and refused in `import_only`, exactly like local_blast_db. Nothing
leaves the machine: ProstT5 turns the query sequence into 3Di structural tokens locally,
and the search runs against a local structure database.

The point of this provider is the one the project already named but never built:
`capability_matrix` lists `local_foldseek` with `executor: not_bundled`. On Linux a
native Foldseek binary plus a downloaded pdb100/cath50 database gives family-T structural
evidence with no AlphaFold step and no web handoff -- the evidence that lets a real RBP
reach R1 and that separates a true binder from an apparatus protein that merely sits in
the tail.

It is deliberately NOT a new reading of the data. Results are normalised into the SAME
dict shape `rbpfinder.parse.foldseek.read` returns from the Foldseek web JSON, written to
disk as that same JSON, and registered under manifest kind `foldseek_json`. So
`rbpfinder.evidence.structural.build` is the single interpreter for both the web-import
route and this one, and the acquisition mode stays provenance, never evidence.

Results are cached by (database label + database path + query sha256), so a rerun does
not re-search and a benchmark result stays reproducible. An empty result is cached and
written too: "searched and found nothing" is a different state from "never searched", and
the manifest layer depends on the difference being visible on disk.
"""
import hashlib
import json
import pathlib
import subprocess
import tempfile

from ..capability import FoldseekCapability
from ..errors import CapabilityUnavailable

PROVIDER_ID = "local_foldseek"

FoldseekUnavailable = CapabilityUnavailable


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class StructureDatabase(object):
    """One configured structure database: a Foldseek db prefix and the label its hits
    carry into Stage 3.

    The label matters and is validated by the caller: `structural.consensus` keeps only
    hits whose database is in `stage3.functional_databases`, so a db registered under a
    name Stage 3 does not recognise would be searched and then silently dropped. Better
    to refuse it up front.
    """

    def __init__(self, label, path):
        self.label = str(label)
        self.path = str(path)


class LocalFoldseek(object):
    def __init__(self, databases, prostt5_model, foldseek_exe=None, cache_dir=None,
                 sensitivity=9.5, threads=4, capability=None, rules=None):
        cap = capability or FoldseekCapability(foldseek=foldseek_exe)
        self.capability = cap
        self.exe = pathlib.Path(cap.require("foldseek")["foldseek"])
        self.databases = list(databases)
        self.prostt5_model = str(prostt5_model) if prostt5_model else None
        self.sensitivity = sensitivity
        self.threads = threads
        self.cache_dir = pathlib.Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        base = "stage3.local_foldseek"
        self.columns = (rules.get(base + ".format_output") if rules else None) or [
            "query", "target", "theader", "prob", "evalue", "bits", "fident",
            "qstart", "qend", "tstart", "tend", "qlen", "taxname"]

    # -- normalisation -----------------------------------------------------
    def _row_to_alignment(self, cols):
        """One Foldseek easy-search row -> one alignment dict in web-JSON field names.

        `rbpfinder.parse.foldseek.read` reads target, prob, eval, score, seqId,
        qStartPos, qEndPos, dbStartPos, dbEndPos, qLen, taxName. The target string there
        carries "<identifier> <description>", and `_split_target` recovers the
        description that Stage 3's direction rule classifies -- so we join the Foldseek
        `target` and `theader` with a single space to reproduce exactly that.
        """
        d = dict(zip(self.columns, cols))

        def num(key):
            v = d.get(key, "")
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        def intt(key):
            v = d.get(key, "")
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return None

        ident = d.get("target", "")
        header = d.get("theader", "")
        target = ("%s %s" % (ident, header)).strip() if header else ident
        return d.get("query", ""), {
            "target": target,
            "prob": num("prob"),
            "eval": num("evalue"),
            "score": num("bits"),
            "seqId": num("fident"),
            "qStartPos": intt("qstart"),
            "qEndPos": intt("qend"),
            "dbStartPos": intt("tstart"),
            "dbEndPos": intt("tend"),
            "qLen": intt("qlen"),
            "taxName": d.get("taxname", ""),
        }

    def _web_json(self, per_db_alignments):
        """Assemble the Foldseek-web-shaped document for one CDS.

        {"results": [{"db": <label>, "alignments": [[aln, ...]]}, ...]}
        """
        return {"results": [{"db": db.label, "alignments": [alns]}
                            for db, alns in per_db_alignments]}

    def _run_one_db(self, db, records_with_seq):
        """Search one database for a whole batch. Returns {query_id: [alignment, ...]}."""
        out = {rec["cds_id"]: [] for rec in records_with_seq}
        with tempfile.TemporaryDirectory() as td:
            td = pathlib.Path(td)
            qf = td / "q.faa"
            res = td / "res.m8"
            tmp = td / "tmp"
            tmp.mkdir()
            with qf.open("w", encoding="utf-8", newline="\n") as fh:
                for rec in records_with_seq:
                    fh.write(">%s\n%s\n" % (rec["cds_id"], rec["translation"]))
            cmd = [str(self.exe), "easy-search", str(qf), db.path, str(res), str(tmp),
                   "--format-output", ",".join(self.columns),
                   "-s", str(self.sensitivity),
                   "--threads", str(self.threads)]
            if self.prostt5_model:
                # ProstT5 search of an amino-acid query: the input is sequence, so
                # Foldseek must predict 3Di locally. Without the weights foldseek expects
                # a structure and the search is a different thing entirely.
                cmd += ["--prostt5-model", self.prostt5_model]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise FoldseekUnavailable(
                    "foldseek easy-search failed (%d): %s"
                    % (proc.returncode, (proc.stderr or proc.stdout)[:500]),
                    capability="foldseek")
            if res.exists():
                for line in res.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not line.strip():
                        continue
                    qid, aln = self._row_to_alignment(line.split("\t"))
                    out.setdefault(qid, []).append(aln)
        return out

    def _cache_path(self, key):
        return self.cache_dir / ("%s.json" % key) if self.cache_dir else None

    def search_many(self, records):
        """Search every configured database for a batch of CDS records.

        Returns {cds_id: web_json_document}. The document is the normalised,
        parser-ready artefact; the caller writes it to disk and registers it.
        """
        recs = [r for r in records if r.get("translation")]
        dbs_key = "|".join("%s=%s" % (d.label, d.path) for d in self.databases)

        todo, cached_docs = [], {}
        for rec in recs:
            key = _sha(dbs_key + "|" + rec["translation"])
            cp = self._cache_path(key)
            if cp and cp.exists():
                try:
                    cached_docs[rec["cds_id"]] = json.loads(cp.read_text(encoding="utf-8"))
                    continue
                except (ValueError, OSError):
                    pass
            todo.append((rec, key))

        searched = {}
        if todo:
            per_db_results = [(db, self._run_one_db(db, [r for r, _ in todo]))
                              for db in self.databases]
            for rec, key in todo:
                per_db_alignments = [(db, res.get(rec["cds_id"], []))
                                     for db, res in per_db_results]
                doc = self._web_json(per_db_alignments)
                searched[rec["cds_id"]] = doc
                cp = self._cache_path(key)
                if cp:
                    cp.write_text(json.dumps(doc), encoding="utf-8")

        out = {}
        out.update(cached_docs)
        out.update(searched)
        return out
