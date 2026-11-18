#!/usr/bin/env python3
"""Run the same genomes under several settings and diff what changed.

Built for the vocabulary comparison, but the shape is general: any change that moves
tiers gets measured this way rather than argued about. The pilot that cleared
`--foldseek-all-cds` used the same two-arm design by hand; this makes it repeatable.

    python3 compare_arms.py --manifest genomes.tsv --out-root runs/ \\
            --arm current:--vocabulary=current \\
            --arm unified:--vocabulary=unified \\
            --labels scores.json --hard-negatives SMA_hard_negatives.tsv \\
            --shared=--foldseek-db=pdb100=/workspace/fsdb/pdb \\
            --shared=--prostt5-model=/workspace/fsdb/weights

Write `--shared` and `--arm` values with `=`, not a space: a value starting with a dash
is otherwise read as another option name.

`--manifest` is a TSV with `phage_id` and `genbank` columns (a header is required; any
other columns are ignored). Each arm is a name and the flags that define it. `--shared`
flags are passed to every arm, which is what keeps an arm comparison honest: the arms
must differ in exactly one thing.

Resumes: an arm/genome whose run_result.json already exists is skipped, so a long sweep
survives an interruption. Pass --force to re-run.

What it reports, and why each one:

    tier changes            the direct effect, over EVERY candidate -- a vocabulary
                            change shows up first as an unlabelled false positive
                            losing its tier
    pool changes            a CDS gained or lost as a candidate altogether
    Primary membership      what a user would actually act on
    false-complete          the invariant that must never break: a run claiming
                            completeness while a reference RBP is absent
    reference RBP rank      regression in complete-recall@K hides here
    hard-negative ceilings  a change that lifts a known negative is not an improvement

It writes `comparison.json` with everything, and prints the summary. It makes no
judgement about whether a change is good -- that is the reader's call, and the numbers
are there to make it an informed one.
"""
import argparse
import csv
import json
import pathlib
import subprocess
import sys


def read_manifest(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        missing = [c for c in ("phage_id", "genbank") if c not in (reader.fieldnames or [])]
        if missing:
            sys.exit("manifest is missing column(s): %s" % ", ".join(missing))
        for r in reader:
            if r.get("phage_id") and r.get("genbank"):
                rows.append((r["phage_id"].strip(), r["genbank"].strip()))
    if not rows:
        sys.exit("manifest has no usable rows")
    return rows


def parse_arm(spec):
    if ":" not in spec:
        sys.exit("--arm wants name:flags, e.g. unified:--vocabulary=unified")
    name, flags = spec.split(":", 1)
    return name.strip(), [f for f in flags.split() if f]


def run_one(phage_id, genbank, outdir, flags, shared, force):
    result = outdir / "run_result.json"
    if result.exists() and not force:
        return "skipped", result
    outdir.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["rbpfinder", "--genbank", genbank, "--phage-id", phage_id,
           "--out", str(outdir), "--quiet-progress"] + shared + flags
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not result.exists():
        return ("failed(exit %d): %s"
                % (proc.returncode, (proc.stderr or proc.stdout)[-300:]), None)
    return "ok", result


def score(result_path, refs, negatives):
    """Everything the comparison needs out of one run, and nothing else."""
    d = json.loads(pathlib.Path(result_path).read_text(encoding="utf-8"))
    by_id = {c["cds_id"]: c for c in d.get("candidates") or []}
    # the ranking is the order stage7 produced; position in it is the rank
    order = [c["cds_id"] for c in d.get("candidates") or []]
    primary = [c if isinstance(c, str) else c.get("cds_id")
               for c in d.get("primary_set") or []]
    out = {
        "vocabulary": (d.get("vocabulary") or {}).get("mode"),
        "completeness": d.get("completeness_assessment"),
        "assessment_coverage": d.get("assessment_coverage"),
        "n_candidates": len(order),
        "primary": primary,
        "terminated_cleanly": (d.get("completeness_audit") or {}).get(
            "terminated_cleanly"),
        "tiers": {}, "ranks": {},
    }
    # Tiers for EVERY candidate, not just the labelled ones: a vocabulary change shows
    # up first as an unlabelled false positive losing its tier, and scoping this to the
    # label set made that invisible in the headline count.
    for cid, c in by_id.items():
        out["tiers"][cid] = "%s-%s" % (c.get("tier_r"), c.get("tier_e"))
    # Ranks stay label-scoped: a rank move matters when we know what the CDS is.
    for cid in sorted(set(refs or []) | set(negatives or [])):
        if cid in by_id:
            out["ranks"][cid] = order.index(cid) + 1
    out["pool"] = sorted(by_id)
    # false-complete: claiming completeness while a reference RBP is not a candidate
    missing = [c for c in (refs or []) if c not in by_id]
    out["false_complete"] = bool(missing) and out["completeness"] == "complete"
    out["reference_absent"] = missing
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="TSV: phage_id, genbank")
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--arm", action="append", required=True,
                    help="name:flags; repeat. Arms must differ in exactly one thing.")
    ap.add_argument("--shared", action="append", default=[],
                    help="flag passed to EVERY arm; repeat. Use --shared=--flag, "
                         "with an equals sign: a value starting with a dash is "
                         "otherwise read as another option name.")
    ap.add_argument("--labels", help="JSON {phage_id: {reference: [cds_id, ...]}}")
    ap.add_argument("--hard-negatives",
                    help="TSV with phage_id, cds_id and optionally ceiling")
    ap.add_argument("--force", action="store_true", help="re-run completed arms")
    args = ap.parse_args(argv)

    genomes = read_manifest(args.manifest)
    arms = [parse_arm(a) for a in args.arm]
    if len(arms) < 2:
        sys.exit("give at least two --arm values; one arm is not a comparison")
    shared = []
    for f in args.shared:
        shared.extend(f.split() if " " in f else [f])

    labels = json.loads(pathlib.Path(args.labels).read_text()) if args.labels else {}
    negatives, ceilings = {}, {}
    if args.hard_negatives:
        with open(args.hard_negatives, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                pid, cid = r.get("phage_id"), r.get("cds_id")
                if pid and cid:
                    negatives.setdefault(pid, []).append(cid)
                    if r.get("ceiling"):
                        ceilings[(pid, cid)] = r["ceiling"].strip()

    root = pathlib.Path(args.out_root)
    report = {"arms": [n for n, _ in arms], "shared_flags": shared, "genomes": {}}

    for pid, gbk in genomes:
        refs = (labels.get(pid) or {}).get("reference") or []
        negs = negatives.get(pid) or []
        report["genomes"][pid] = {}
        for name, flags in arms:
            status, res = run_one(pid, gbk, root / name / pid, flags, shared, args.force)
            print("%-14s %-22s %s" % (name, pid, status), flush=True)
            report["genomes"][pid][name] = (
                score(res, refs, negs) if res else {"error": status})

    # ---- differences ----------------------------------------------------------
    base = report["arms"][0]
    diffs = {"tier_changes": [], "primary_changes": [], "pool_changes": [],
             "false_complete": [], "hard_negative_lifted": [], "rank_moves": []}
    for pid, by_arm in report["genomes"].items():
        b = by_arm.get(base) or {}
        if "error" in b:
            continue
        for name in report["arms"][1:]:
            o = by_arm.get(name) or {}
            if "error" in o:
                continue
            for cid, t in (o.get("tiers") or {}).items():
                if b.get("tiers", {}).get(cid) not in (None, t):
                    diffs["tier_changes"].append(
                        {"phage": pid, "arm": name, "cds_id": cid,
                         base: b["tiers"][cid], name: t})
                    ceiling = ceilings.get((pid, cid))
                    if ceiling and t.split("-")[0] != ceiling and cid in (
                            negatives.get(pid) or []):
                        diffs["hard_negative_lifted"].append(
                            {"phage": pid, "arm": name, "cds_id": cid,
                             "ceiling": ceiling, "got": t})
            if sorted(b.get("pool") or []) != sorted(o.get("pool") or []):
                gained = sorted(set(o.get("pool") or []) - set(b.get("pool") or []))
                lost = sorted(set(b.get("pool") or []) - set(o.get("pool") or []))
                diffs["pool_changes"].append(
                    {"phage": pid, "arm": name, "gained": gained, "lost": lost})
            if sorted(b.get("primary") or []) != sorted(o.get("primary") or []):
                diffs["primary_changes"].append(
                    {"phage": pid, "arm": name,
                     base: sorted(b.get("primary") or []),
                     name: sorted(o.get("primary") or [])})
            for cid, rk in (o.get("ranks") or {}).items():
                if b.get("ranks", {}).get(cid) not in (None, rk):
                    diffs["rank_moves"].append(
                        {"phage": pid, "arm": name, "cds_id": cid,
                         base: b["ranks"][cid], name: rk})
            if o.get("false_complete"):
                diffs["false_complete"].append({"phage": pid, "arm": name})
        if b.get("false_complete"):
            diffs["false_complete"].append({"phage": pid, "arm": base})
    report["differences"] = diffs

    out = root / "comparison.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")

    print()
    print("=== summary (base arm: %s) ===" % base)
    print("  genomes            %d" % len(report["genomes"]))
    print("  false-complete     %d   <- must be 0 in EVERY arm"
          % len(diffs["false_complete"]))
    print("  hard neg lifted    %d   <- must be 0; a lifted negative is not an improvement"
          % len(diffs["hard_negative_lifted"]))
    print("  tier changes       %d   <- all candidates, not just labelled ones"
          % len(diffs["tier_changes"]))
    print("  pool changes       %d   <- a CDS gained or lost as a candidate entirely"
          % len(diffs["pool_changes"]))
    print("  Primary changes    %d   <- what a user would act on" % len(diffs["primary_changes"]))
    print("  reference moves    %d" % len(diffs["rank_moves"]))
    print()
    print("  written: %s" % out)
    for k in ("false_complete", "hard_negative_lifted"):
        for row in diffs[k]:
            print("  !! %s %s" % (k, json.dumps(row, sort_keys=True)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
