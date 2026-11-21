#!/usr/bin/env python3
"""Measure the presentation vocabulary's noise floor against data already on disk.

    python3 term_noise_floor.py --aggregate runs/          # across many runs
    python3 term_noise_floor.py runs/*/stage3_local        # against raw hit titles

`--aggregate` is the mode for a large-scale round: it rolls up the
`term_trigger_counts` every run already records, so the calibration comes out as a
by-product with no extra step. Use the other mode to look at the titles themselves when
a term's behaviour needs explaining rather than counting.

`presentation_signals` is `calibration: pending`. Eight assembly-context words are in the
vocabulary and, as of 2026-11-21, only `baseplate` has fired on a true signal. The other
seven are **unmeasured, not shown useful** -- and the right way to measure them is not to
run more genomes. It is to count how often each word appears in structure hit titles at
all, because that is the noise floor: a word occurring in a large fraction of PDB titles
will fire on nearly every candidate whatever phage you give it.

Every run already wrote `stage3_local/<cds>.foldseek.json` for every CDS it searched,
hit titles included. So the measurement costs no CPU and no new strains.

What it reports, per term:

    titles          how many hit titles contain it, over how many
    CDS             how many CDS have at least one such title
    co-occurs with  which other vocabulary terms appear in the same titles

That last column is what justified pruning `complex`: its hits were titles like "Complex
of Mu phage tail fiber and its chaperone", where the real discriminator (`baseplate`) was
absent and the match was a chaperone recognising its own structure. A term that only ever
co-occurs with a stronger one is redundant; a term that fires alone and often is noise.

This reads; it changes nothing and writes nothing.
"""
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
_B = "presentation_signals"


def vocabulary():
    sys.path.insert(0, str(ROOT))
    from rbpfinder.config import Rules
    r = Rules(ROOT / "rules" / "decision_rules.yaml")
    return [str(x).lower() for x in
            (r.get(_B + ".structure_annotation_consistency.assembly_context_terms") or ())]


def titles_from(path):
    """Every (cds_id, title) a foldseek artifact or a run directory holds."""
    p = pathlib.Path(path)
    files = []
    if p.is_dir():
        files = sorted(p.rglob("*.foldseek.json"))
    elif p.name.endswith(".foldseek.json"):
        files = [p]
    elif p.name == "run_result.json":
        # the run's own copy of the top hit per CDS, when the artifacts are gone
        d = json.loads(p.read_text(encoding="utf-8"))
        out = []
        for c in d.get("candidates") or []:
            for rec in c.get("evidence") or []:
                t = ((rec.get("raw") or {}).get("hit_description") or "").strip()
                if t:
                    out.append((c["cds_id"], t))
        return out

    out = []
    for f in files:
        cid = f.name[: -len(".foldseek.json")]
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for res in doc.get("results") or []:
            for aln_group in res.get("alignments") or []:
                for aln in (aln_group if isinstance(aln_group, list) else [aln_group]):
                    if not isinstance(aln, dict):
                        continue
                    for key in ("tdescription", "theader", "target", "tsetid"):
                        t = str(aln.get(key) or "").strip()
                        if t:
                            out.append((cid, t))
                            break
    return out


def aggregate_runs(paths):
    """Roll up `presentation_signals.term_trigger_counts` across many runs.

    The point of this mode: a large-scale application round produces the calibration
    data as a BY-PRODUCT, with no extra step and no extra CPU. Every run already records
    which terms fired and how often, so a sweep over N genomes answers "is this word
    earning its place" far better than the two genomes the vocabulary was seeded from.

    A term silent across many genomes removes itself. A term firing on nearly every
    candidate removes itself the other way. Neither conclusion needs a decision from
    anyone -- which is the point of `calibration: pending` being a status rather than an
    apology.
    """
    runs, totals, fired_in = 0, collections.Counter(), collections.Counter()
    examined, with_signal, per_run = 0, 0, []
    for path in paths:
        p = pathlib.Path(path)
        files = sorted(p.rglob("run_result.json")) if p.is_dir() else [p]
        for f in files:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            block = d.get("presentation_signals")
            if not isinstance(block, dict):
                continue
            runs += 1
            examined += block.get("candidates_examined") or 0
            with_signal += block.get("candidates_with_any_signal") or 0
            counts = block.get("term_trigger_counts") or {}
            per_run.append((d.get("phage_id") or f.parent.name, counts))
            for term, n in counts.items():
                totals[term] += n
                fired_in[term] += 1
    return runs, totals, fired_in, examined, with_signal, per_run


def report_aggregate(paths):
    terms = vocabulary()
    runs, totals, fired_in, examined, with_signal, per_run = aggregate_runs(paths)
    if not runs:
        sys.exit("found no run_result.json carrying a presentation_signals block in: %s"
                 % ", ".join(paths))

    print("%d run(s), %d candidates examined, %d carried at least one signal (%.1f%%)"
          % (runs, examined, with_signal,
             100.0 * with_signal / max(examined, 1)))
    print()
    print("%-20s %8s %8s %9s  %s"
          % ("term", "hits", "runs", "runs %", "verdict on this evidence"))
    for term in sorted(terms, key=lambda x: (-totals[x], x)):
        share = 100.0 * fired_in[term] / runs
        if not totals[term]:
            verdict = "silent -- remove if this sweep is representative"
        elif share > 80.0:
            verdict = "fires almost everywhere -- likely noise"
        elif totals[term] == 1:
            verdict = "one hit -- still a single observation"
        else:
            verdict = "earning its place"
        print("%-20s %8d %8d %8.0f%%  %s"
              % (term, totals[term], fired_in[term], share, verdict))

    extra = sorted(set(totals) - set(terms))
    if extra:
        print()
        print("counted but not in the current vocabulary (pruned since these runs): %s"
              % ", ".join(extra))
    print()
    print("Terms are PRESENTATION ONLY. Nothing above changed a tier, a family or a")
    print("candidate set, so pruning or keeping a word here costs attention, not")
    print("correctness. See presentation_signals in rules/decision_rules.yaml.")
    return 0


def main(argv):
    if not argv:
        sys.exit("usage: term_noise_floor.py <stage3_local dir | run dir | *.json> ...")
    if argv[0] == "--aggregate":
        return report_aggregate(argv[1:] or ["."])
    terms = vocabulary()
    if not terms:
        sys.exit("the vocabulary is empty; nothing to measure")

    pairs = []
    for arg in argv:
        pairs.extend(titles_from(arg))
    if not pairs:
        sys.exit("found no hit titles in: %s" % ", ".join(argv))

    n_titles = len(pairs)
    cds = {c for c, _ in pairs}
    hit_titles = collections.Counter()
    hit_cds = collections.defaultdict(set)
    co = collections.defaultdict(collections.Counter)
    alone = collections.Counter()

    for cid, title in pairs:
        low = title.lower()
        present = [t for t in terms if t in low]
        for t in present:
            hit_titles[t] += 1
            hit_cds[t].add(cid)
            for other in present:
                if other != t:
                    co[t][other] += 1
            if len(present) == 1:
                alone[t] += 1

    print("titles %d over %d CDS, vocabulary of %d terms"
          % (n_titles, len(cds), len(terms)))
    print()
    print("%-18s %8s %7s %7s %7s  %s"
          % ("term", "titles", "% ", "CDS", "alone", "co-occurs with"))
    for t in sorted(terms, key=lambda x: (-hit_titles[x], x)):
        pct = 100.0 * hit_titles[t] / n_titles
        others = ", ".join("%s(%d)" % (o, n) for o, n in co[t].most_common(3)) or "--"
        print("%-18s %8d %6.2f%% %7d %7d  %s"
              % (t, hit_titles[t], pct, len(hit_cds[t]), alone[t], others))

    print()
    silent = [t for t in terms if not hit_titles[t]]
    if silent:
        print("never appears in any title: %s" % ", ".join(silent))
        print("  -> dead weight here. Either these titles are not the right corpus, or")
        print("     the words can go. Check against a second genome before pruning.")
    noisy = [t for t in terms if hit_titles[t] and
             100.0 * hit_titles[t] / n_titles > 5.0]
    if noisy:
        print("in over 5%% of titles: %s" % ", ".join(noisy))
        print("  -> likely noise: a word this common will fire on nearly every")
        print("     candidate, whatever phage it is.")
    redundant = [t for t in terms
                 if hit_titles[t] and not alone[t] and co[t]]
    if redundant:
        print("never appears without another vocabulary term: %s" % ", ".join(redundant))
        print("  -> redundant: the co-occurring term already carries the signal. This is")
        print("     the argument that pruned `complex`.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
