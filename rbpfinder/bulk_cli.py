"""`rbpfinder bulk ...` -- run, then merge/apply manual-provider evidence, across many
genomes without a separate manual round per genome. M22-d.

Two platform facts settled the shape of this before writing it. HH-suite (HHpred) and
Foldseek have no native Windows build -- confirmed from HH-suite's own GitHub releases
(Linux/conda/Docker only) and the already-documented Foldseek constraint. This project
already avoids WSL. So "download the database, run locally" is not achievable for those
two providers here, the same wall AlphaFold's GPU requirement already is. This command
does not attempt to route around that: it automates what is already automatable (local
BLAST; InterProScan via the existing `AUTOMATED_REMOTE` path, consent required exactly as
for a single run) and collapses everything that still needs HHpred/Foldseek into ONE
combined manual round across every genome in the sweep, instead of one round per genome.

Nothing here is a new acquisition mode or a new parser. `bulk merge-batches` and
`bulk apply-returns` operate purely on files `rbpfinder run` already writes
(`evidence_acquisition.write_batch` / `write_return_template`) and re-invoke the existing
`cli.main()` once per genome -- the exact same entrypoint a direct command-line
`rbpfinder run` would use, called in-process rather than by shelling out.

Two things make merging safe without any new routing logic:

    cds_id is already globally unique across genomes (phage-id-prefixed), so
    concatenating N genomes' batch rows into one file has zero collision risk.

    return_binding.check() already fails SAFELY on a foreign row: a cds_id not in the
    current run's candidate pool becomes UNKNOWN_CANDIDATE -- reported, never silently
    misattributed. So the same merged return manifest can be handed to every genome's
    own --out directory in turn; each genome accepts only its own rows.
"""
import csv
import json
import pathlib
import sys

from . import evidence_acquisition as evacq
from .config import Rules
from .errors import InputValidationError

_RESOURCES_RULES = None


def _rules():
    global _RESOURCES_RULES
    if _RESOURCES_RULES is None:
        from . import resources as _resources
        _RESOURCES_RULES = Rules(_resources.default_rules_path())
    return _RESOURCES_RULES


def _strip_flags(argv, names):
    """argv with each `--flag value` pair in `names` removed; everything else kept, in
    order, to pass straight through to the per-genome `rbpfinder run` invocation."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] in names:
            i += 2          # the flag and its value
            continue
        out.append(argv[i])
        i += 1
    return out


def _read_manifest(path):
    """genomes.tsv: phage_id, genbank_path, and an optional free-form extra_args column.

    extra_args is kept as ONE column rather than one column per possible run flag,
    matching how batch_contract itself stays minimal -- enumerating every `rbpfinder run`
    flag here would give this file a second, drifting copy of that parser's own schema.
    """
    p = pathlib.Path(path)
    if not p.exists():
        raise InputValidationError("manifest not found: %s" % p)
    with p.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        missing = [c for c in ("phage_id", "genbank_path")
                  if c not in (reader.fieldnames or [])]
        if missing:
            raise InputValidationError(
                "%s is missing required column%s %s"
                % (p.name, "" if len(missing) == 1 else "s",
                   ", ".join("'%s'" % c for c in missing)))
        rows = [r for r in reader if (r.get("phage_id") or "").strip()]
    if not rows:
        raise InputValidationError("%s has no genome rows" % p.name)
    seen = set()
    for r in rows:
        pid = r["phage_id"].strip()
        if pid in seen:
            raise InputValidationError(
                "%s lists phage_id %r more than once -- --out-root/<phage_id> would "
                "collide" % (p.name, pid))
        seen.add(pid)
    return rows


def _run(argv):
    if "--manifest" not in argv or "--out-root" not in argv:
        raise InputValidationError(
            "usage: rbpfinder bulk run --manifest <genomes.tsv> --out-root <dir> "
            "[shared rbpfinder run flags]")
    manifest_path = argv[argv.index("--manifest") + 1]
    out_root = pathlib.Path(argv[argv.index("--out-root") + 1])
    shared = _strip_flags(argv, ("--manifest", "--out-root"))
    rows = _read_manifest(manifest_path)
    out_root.mkdir(parents=True, exist_ok=True)

    from . import cli as _cli                                       # noqa: E402, lazy
    results = []
    for r in rows:
        pid = r["phage_id"].strip()
        gbk = r["genbank_path"].strip()
        out_dir = out_root / pid
        extra = (r.get("extra_args") or "").split()
        genome_argv = (["--genbank", gbk, "--phage-id", pid, "--out", str(out_dir)]
                      + shared + extra)
        print("=== %s ===" % pid, file=sys.stderr)
        try:
            rc = _cli.main(genome_argv)
        except Exception as exc:                                    # noqa: BLE001
            # One genome's unexpected failure must never abort the sweep silently --
            # every other genome in the manifest still gets its own attempt, and the
            # failure is collected and reported at the end, not swallowed.
            rc, exc_detail = 1, "%s: %s" % (type(exc).__name__, exc)
        else:
            exc_detail = None
        tiers = {}
        rr = out_dir / "run_result.json"
        if rr.exists():
            try:
                data = json.loads(rr.read_text(encoding="utf-8"))
                for c in data.get("candidates") or []:
                    tiers[c.get("tier_r")] = tiers.get(c.get("tier_r"), 0) + 1
            except Exception:                                        # noqa: BLE001
                pass
        results.append({"phage_id": pid, "exit_code": rc, "tier_counts": tiers,
                        "error": exc_detail})

    print()
    print("%-24s %6s  %s" % ("phage_id", "exit", "tier_r counts"))
    failed = []
    for r in results:
        print("%-24s %6d  %s%s" % (r["phage_id"], r["exit_code"], r["tier_counts"],
                                   (" -- %s" % r["error"]) if r["error"] else ""))
        if r["exit_code"] != 0:
            failed.append(r["phage_id"])
    print()
    print("%d genome(s), %d failed%s" % (len(results), len(failed),
                                         (": %s" % ", ".join(failed)) if failed else ""))
    (out_root / "BULK_RUN_SUMMARY.json").write_text(
        json.dumps({"manifest": str(manifest_path), "results": results}, indent=2)
        + "\n", encoding="utf-8")
    return 1 if failed else 0


def _merge_batches(argv):
    if not all(f in argv for f in ("--manifest", "--out-root", "--provider")):
        raise InputValidationError(
            "usage: rbpfinder bulk merge-batches --manifest <genomes.tsv> "
            "--out-root <dir> --provider <name>")
    manifest_path = argv[argv.index("--manifest") + 1]
    out_root = pathlib.Path(argv[argv.index("--out-root") + 1])
    provider = argv[argv.index("--provider") + 1]
    rules = _rules()
    p = evacq.spec(provider, rules)
    batch_name = p.get("manual_batch_file")
    if not batch_name:
        raise InputValidationError(
            "%s has no manual batch file (%s)" % (provider, p.get("why_default", "")))

    rows_all, fields, contributing, empty = [], None, [], []
    for r in _read_manifest(manifest_path):
        pid = r["phage_id"].strip()
        bp = out_root / pid / batch_name
        if not bp.exists():
            continue
        with bp.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh, delimiter="\t")
            fields = fields or list(reader.fieldnames or [])
            genome_rows = list(reader)
        if genome_rows:
            rows_all.extend(genome_rows)
            contributing.append(pid)
        else:
            empty.append(pid)          # header-only: nothing owed there, not an error

    if fields is None:
        print("no genome in this manifest has ever written a %s batch file -- run "
              "'rbpfinder bulk run' first" % batch_name)
        return 1

    out_root.mkdir(parents=True, exist_ok=True)
    merged_batch = out_root / ("MERGED_%s" % batch_name)
    with merged_batch.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(fields) + "\n")
        for row in rows_all:
            fh.write("\t".join(str(row.get(f, "")) for f in fields) + "\n")

    # Reuses write_return_template UNCHANGED -- the TSV rows just re-read carry exactly
    # the shape (candidate_id, sequence_sha256, batch_id, ...) batch_rows() would have
    # produced fresh, so the existing function is called directly rather than
    # re-implementing its row-shaping.
    merged_template = evacq.write_return_template(provider, rows_all, out_root, rules)

    print("provider           : %s (%s)" % (provider, p.get("service_name", provider)))
    print("genomes contributing: %d  %s" % (len(contributing), contributing))
    print("genomes with nothing owed: %d  %s" % (len(empty), empty))
    distinct_ids = len({row.get("candidate_id") for row in rows_all})
    print("candidate rows merged: %d" % len(rows_all))
    print("distinct cds_id      : %d%s"
          % (distinct_ids,
             "" if distinct_ids == len(rows_all) else
             "  *** COLLISION -- a cds_id repeats across genomes, which should be "
             "impossible; investigate the manifest before submitting anything"))
    print()
    print("written: %s" % merged_batch)
    if merged_template:
        print("written: %s  (fill in `path`, one submission covers every genome above)"
              % merged_template)
    else:
        print("no return template written -- %s has no importable return_kinds "
              "(%s)" % (provider, p.get("return_via", "n/a")))
    return 0


def _apply_returns(argv):
    if not all(f in argv for f in ("--manifest", "--out-root", "--stage2-manifest")):
        raise InputValidationError(
            "usage: rbpfinder bulk apply-returns --manifest <genomes.tsv> "
            "--out-root <dir> --stage2-manifest <merged_return.tsv> "
            "[shared rbpfinder run flags]")
    manifest_path = argv[argv.index("--manifest") + 1]
    out_root = pathlib.Path(argv[argv.index("--out-root") + 1])
    stage2_manifest = argv[argv.index("--stage2-manifest") + 1]
    shared = _strip_flags(argv, ("--manifest", "--out-root", "--stage2-manifest"))

    from . import cli as _cli                                       # noqa: E402, lazy
    results = []
    for r in _read_manifest(manifest_path):
        pid = r["phage_id"].strip()
        gbk = r["genbank_path"].strip()
        out_dir = out_root / pid
        if not (out_dir / "run_result.json").exists():
            results.append({"phage_id": pid, "skipped": "never run (bulk run first)"})
            continue
        extra = (r.get("extra_args") or "").split()
        # Deliberately NOT --restart-stage2: resume is already the default, and applying
        # a merged manifest is exactly the "only what's missing" case it exists for.
        genome_argv = (["--genbank", gbk, "--phage-id", pid, "--out", str(out_dir),
                        "--stage2-manifest", stage2_manifest] + shared + extra)
        print("=== %s ===" % pid, file=sys.stderr)
        try:
            rc = _cli.main(genome_argv)
        except Exception as exc:                                    # noqa: BLE001
            rc, exc_detail = 1, "%s: %s" % (type(exc).__name__, exc)
        else:
            exc_detail = None
        results.append({"phage_id": pid, "exit_code": rc, "error": exc_detail})

    print()
    print("%-24s %s" % ("phage_id", "result"))
    for r in results:
        if "skipped" in r:
            print("%-24s SKIPPED (%s)" % (r["phage_id"], r["skipped"]))
        else:
            print("%-24s exit=%d%s" % (r["phage_id"], r["exit_code"],
                                       (" -- %s" % r["error"]) if r["error"] else ""))
    failed = [r["phage_id"] for r in results
             if "exit_code" in r and r["exit_code"] != 0]
    return 1 if failed else 0


def main(argv=None):
    argv = list(sys.argv[2:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: rbpfinder bulk run --manifest <genomes.tsv> --out-root <dir> "
              "[run flags]")
        print("       rbpfinder bulk merge-batches --manifest <genomes.tsv> "
              "--out-root <dir> --provider <name>")
        print("       rbpfinder bulk apply-returns --manifest <genomes.tsv> "
              "--out-root <dir> --stage2-manifest <merged_return.tsv> [run flags]")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "run":
        return _run(rest)
    if cmd == "merge-batches":
        return _merge_batches(rest)
    if cmd == "apply-returns":
        return _apply_returns(rest)
    raise InputValidationError(
        "unknown bulk command %r (expected run, merge-batches or apply-returns)" % cmd)
