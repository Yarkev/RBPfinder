"""C5 -- comparative genomics: variable loci in an otherwise conserved backbone.

The signature being looked for is positional, not sequence-based:

    conserved -- conserved -- VARIABLE -- conserved -- conserved
                                 ^
                        host-range determinant candidate

A protein that is simply absent from relatives proves nothing; a protein that is
absent or replaced while BOTH its neighbours are present in nearly every relative
is a swapped module. That is the shape of a tail-fibre tip exchange, and it is the
one signal that does not depend on anybody having annotated the protein.

Two rules govern this module and neither is negotiable:

1. **The cohort is chosen without touching the answer.** Comparators are selected by
   shared gene content -- never by the benchmark's RBP-set clusters, which are DEFINED
   by the ground truth. Choosing who to compare against using the labels would let the
   truth pick its own exam questions.

2. **Variability is not receptor binding.** v1 emits `supports_apparatus_membership`
   and the YAML forbids `supports_receptor_binding` outright. Anti-defence modules,
   immunity determinants and ordinary structural tail proteins all sit in variable
   loci. A stronger direction has to wait for host-range data that co-segregates with
   the orthogroup.

Absence of comparative data is a THIRD state, never a failure: see
`stage6.a4_capability.states`.
"""
import collections
import pathlib
import re
import subprocess
import tempfile

from ..capability import BlastCapability

STATE_NOT_RUN = "not_run"
STATE_NO_COHORT = "implemented_but_no_comparator_data"
STATE_OK = "executable"


def _module_of(rec, rules):
    mm = rules.get("module_map")
    return mm.get(rec["function"], mm.get("_default"))


def _neighbours(idx, n, total, circular):
    """Indices within +/- n, honouring circular adjacency."""
    out = []
    for d in list(range(-n, 0)) + list(range(1, n + 1)):
        j = idx + d
        if circular:
            j %= total
        elif j < 0 or j >= total:
            continue
        out.append(j)
    return out


# --------------------------------------------------------------------------
# cohort selection
# --------------------------------------------------------------------------
def shared_gene_content(target_hits, target_n, cohort_ids):
    """Fraction of the target's proteins with a hit in each comparator genome."""
    out = {}
    for pid in cohort_ids:
        n = sum(1 for cid, per in target_hits.items() if pid in per)
        out[pid] = n / float(target_n) if target_n else 0.0
    return out


def clone_groups(hits, comparators, rules):
    """Collapse comparators that are the same genome to one vote.

    Two comparators belong to one clone group when they hit the same target CDS and
    agree on identity almost everywhere. The identity vectors already exist from the
    cohort search, so this costs no extra BLAST and reads no taxonomy, filename or
    label -- ten copies of one genome are one observation, not ten.
    """
    base = "stage1.channels.C5_comparative_variable_locus.clone_group_dedup"
    tol = rules.get(base + ".identity_tolerance_pp")
    min_overlap = rules.get(base + ".min_profile_overlap")

    prof = {pid: {cid: per[pid]["identity_pct"]
                  for cid, per in hits.items() if pid in per}
            for pid in comparators}

    groups = []
    for pid in comparators:
        placed = False
        for g in groups:
            rep = g[0]
            a, b = prof[pid], prof[rep]
            shared_cds = set(a) & set(b)
            union = set(a) | set(b)
            if not union:
                continue
            if len(shared_cds) / float(len(union)) < min_overlap:
                continue
            delta = sum(abs(a[c] - b[c]) for c in shared_cds) / float(len(shared_cds))
            if delta <= tol:
                g.append(pid)
                placed = True
                break
        if not placed:
            groups.append([pid])
    return groups


def select_cohort(shared, rules, hits=None, near_clone_fallback=False):
    """Label-free comparator choice.

    Primary band first. The ultra-close set is consulted ONLY when the primary band
    cannot fill a cohort, and only when this run authorised the experimental v1.1
    branch -- a database full of near-clones must never be able to crowd out
    comparators that carry real phylogenetic spread.
    """
    base = "stage1.channels.C5_comparative_variable_locus.cohort_selection"
    lo = rules.get(base + ".min_shared_fraction")
    hi = rules.get(base + ".max_shared_fraction")
    lo_n = rules.get(base + ".min_cohort")
    hi_n = rules.get(base + ".max_cohort")
    eligible = [(pid, f) for pid, f in shared.items() if lo <= f <= hi]
    eligible.sort(key=lambda x: -x[1])
    chosen = [pid for pid, _ in eligible[:hi_n]]
    detail = {
        "considered": len(shared),
        "band": [lo, hi],
        "eligible": len(eligible),
        "chosen": chosen,
        "cohort_source": "primary_band",
        "fallback_used": False,
        "clone_groups_collapsed": 0,
        "shared_fraction": {pid: round(shared[pid], 4) for pid in chosen},
        "excluded_too_similar": sorted(pid for pid, f in shared.items() if f > hi),
        "excluded_too_distant": sorted(pid for pid, f in shared.items() if f < lo),
    }
    if len(chosen) >= lo_n:
        return chosen, detail

    if not near_clone_fallback:
        return [], detail

    fb = ("stage1.channels.C5_comparative_variable_locus.ultra_close_fallback")
    f_lo = rules.get(fb + ".min_shared_fraction")
    f_hi = rules.get(fb + ".max_shared_fraction")
    f_max = rules.get(fb + ".max_members")
    near = [(pid, f) for pid, f in shared.items() if f_lo <= f <= f_hi]
    near.sort(key=lambda x: -x[1])
    candidates = [pid for pid, _ in near]

    # one vote per clone group, before the cohort is capped
    groups = clone_groups(hits or {}, candidates, rules)
    reps = [max(g, key=lambda p: shared.get(p, 0.0)) for g in groups]
    reps.sort(key=lambda p: -shared.get(p, 0.0))
    merged = [pid for pid, _ in eligible] + [p for p in reps if p not in dict(eligible)]
    merged = merged[:f_max]

    detail.update({
        "chosen": merged,
        "cohort_source": "primary_band+ultra_close_fallback",
        "fallback_used": True,
        "ultra_close_band": [f_lo, f_hi],
        "ultra_close_candidates": len(candidates),
        "clone_groups_collapsed": len(candidates) - len(reps),
        "clone_group_sizes": sorted((len(g) for g in groups), reverse=True),
        "shared_fraction": {pid: round(shared[pid], 4) for pid in merged},
    })
    if len(merged) < lo_n:
        detail["chosen"] = []
        return [], detail
    return merged, detail


# --------------------------------------------------------------------------
# orthology
# --------------------------------------------------------------------------
def _write_faa(path, entries):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for name, seq in entries:
            fh.write(">%s\n%s\n" % (name, seq))


def orthologs(target_recs, catalog, blastp_exe, rules, workdir=None, capability=None):
    """{target_cds_id: {comparator_phage_id: best_hit}} for qualifying hits only.

    One blastp against a single database built from every comparator proteome; the
    comparator's phage id is carried in the subject name, so per-genome presence is
    read straight off the hit table.
    """
    base = "stage1.channels.C5_comparative_variable_locus.ortholog_call"
    min_id = rules.get(base + ".min_identity_pct")
    min_cov = rules.get(base + ".min_query_coverage_pct")
    max_e = float(rules.get(base + ".max_evalue"))

    tmp = pathlib.Path(workdir or tempfile.mkdtemp(prefix="rbp_c5_"))
    tmp.mkdir(parents=True, exist_ok=True)
    subj = tmp / "cohort.faa"
    _write_faa(subj, [("%s|%s" % (pid, r["cds_id"]), r["translation"])
                      for pid, recs in catalog.items() for r in recs
                      if r.get("translation")])
    query = tmp / "target.faa"
    _write_faa(query, [(r["cds_id"], r["translation"]) for r in target_recs
                       if r.get("translation")])

    # C5 builds a database, so it needs BOTH executables, and it takes them from the
    # shared resolver. Deriving makeblastdb from the blastp filename -- as this did until
    # M15B-2 -- silently bypassed every capability check the resolver performs.
    cap = capability or BlastCapability(blastp=blastp_exe)
    exes = cap.require("blastp", "makeblastdb")
    blastp_exe = exes["blastp"]
    makeblastdb = exes["makeblastdb"]
    db = tmp / "cohort"
    p = subprocess.run([str(makeblastdb), "-in", str(subj), "-dbtype", "prot",
                        "-out", str(db)], capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("makeblastdb failed for the C5 cohort: %s" % p.stderr[:300])

    out = tmp / "hits.tsv"
    cmd = [str(blastp_exe), "-query", str(query), "-db", str(db), "-out", str(out),
           "-outfmt", "6 qseqid sseqid pident qcovs evalue bitscore",
           "-evalue", str(max_e), "-max_target_seqs", "500", "-num_threads", "4"]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError("blastp failed for the C5 cohort: %s" % p.stderr[:300])

    hits = collections.defaultdict(dict)
    with out.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 6:
                continue
            q, s, pident, qcovs, ev, bits = f[0], f[1], float(f[2]), float(f[3]), \
                float(f[4]), float(f[5])
            if pident < min_id or qcovs < min_cov:
                continue
            pid = s.split("|", 1)[0]
            prev = hits[q].get(pid)
            if prev is None or bits > prev["bitscore"]:
                hits[q][pid] = {"subject": s, "identity_pct": pident,
                                "coverage_pct": qcovs, "evalue": ev, "bitscore": bits}
    return dict(hits), tmp


# --------------------------------------------------------------------------
# the variable-locus scan
# --------------------------------------------------------------------------
def mean_identity(hits, cohort):
    """Mean percent identity to the cohort's orthologs, per target CDS."""
    out = {}
    for cid, per in hits.items():
        vals = [per[p]["identity_pct"] for p in cohort if p in per]
        out[cid] = (sum(vals) / float(len(vals))) if vals else None
    return out


def variable_loci(target_recs, presence, rules, is_circular, identity=None):
    """Find CDS that are variable while their neighbourhood is conserved.

    Returns (nominated, loci, details). A locus is a maximal run of variable CDS
    bounded by conserved CDS on both sides -- the whole run is reported, because a
    swapped tip and its adapter travel together and A4 must be able to see a member
    that the per-CDS test did not nominate.
    """
    base = "stage1.channels.C5_comparative_variable_locus.variability"
    max_self = rules.get(base + ".max_self_presence")
    min_flank = rules.get(base + ".min_flank_presence")
    flank_n = rules.get(base + ".flank_cds")
    need_tail = rules.get(base + ".require_tail_context")
    tail_r = rules.get(base + ".tail_context_radius_cds")

    recs = sorted(target_recs, key=lambda r: (r["start_nt"], r["cds_id"]))
    total = len(recs)
    tail_idx = {i for i, r in enumerate(recs)
                if _module_of(r, rules) == "tail_morphogenesis"}

    min_drop = rules.get(base + ".min_identity_drop_pp")
    min_pres_id = rules.get(base + ".min_presence_for_identity_test")
    min_samples = rules.get(base + ".min_flank_identity_samples")
    identity = identity or {}

    details, variable = {}, set()
    for i, rec in enumerate(recs):
        own = presence.get(rec["cds_id"], 0.0)
        nb = _neighbours(i, flank_n, total, is_circular)
        flank = [presence.get(recs[j]["cds_id"], 0.0) for j in nb]
        flank_mean = sum(flank) / float(len(flank)) if flank else 0.0
        near_tail = bool(tail_idx & set(_neighbours(i, tail_r, total, is_circular))) \
            or i in tail_idx

        # shape 1 -- the module is absent or replaced
        absent = own <= max_self and flank_mean >= min_flank

        # shape 2 -- present everywhere, but far more divergent than its backbone.
        # Measured as a GAP against the local neighbourhood, so a distant cohort does
        # not make every gene look variable and a close one does not mask everything.
        own_id = identity.get(rec["cds_id"])
        flank_ids = [identity.get(recs[j]["cds_id"]) for j in nb]
        flank_ids = [v for v in flank_ids if v is not None]
        drop = None
        if (own_id is not None and own >= min_pres_id
                and len(flank_ids) >= min_samples):
            drop = own_id - (sum(flank_ids) / float(len(flank_ids)))
        divergent = drop is not None and drop <= -min_drop

        is_var = (absent or divergent) and (near_tail or not need_tail)
        details[rec["cds_id"]] = {
            "self_presence": round(own, 4),
            "flank_presence": round(flank_mean, 4),
            "mean_identity_pct": round(own_id, 2) if own_id is not None else None,
            "identity_drop_pp": round(drop, 2) if drop is not None else None,
            "signal": ("absent_or_replaced" if absent else
                       "present_but_divergent" if divergent else None),
            "in_tail_context": near_tail,
            "is_variable": is_var,
        }
        if is_var:
            variable.add(rec["cds_id"])

    # group adjacent variable CDS into loci
    loci, cur = [], []
    for i, rec in enumerate(recs):
        if rec["cds_id"] in variable:
            cur.append(i)
        elif cur:
            loci.append(cur)
            cur = []
    if cur:
        loci.append(cur)
    if is_circular and len(loci) > 1 and loci[0][0] == 0 and loci[-1][-1] == total - 1:
        loci[0] = loci[-1] + loci[0]           # the run wraps the origin
        loci.pop()

    out_loci = []
    for members in loci:
        lo, hi = members[0], members[-1]
        left = recs[(lo - 1) % total] if is_circular or lo > 0 else None
        right = recs[(hi + 1) % total] if is_circular or hi + 1 < total else None
        out_loci.append({
            "locus_id": "VL_%s" % recs[lo]["cds_id"],
            "members": [recs[j]["cds_id"] for j in members],
            "left_conserved": left["cds_id"] if left else None,
            "right_conserved": right["cds_id"] if right else None,
            "left_presence": round(presence.get(left["cds_id"], 0.0), 4) if left else None,
            "right_presence": round(presence.get(right["cds_id"], 0.0), 4) if right else None,
            "in_tail_context": any(details[recs[j]["cds_id"]]["in_tail_context"]
                                   for j in members),
        })
    return variable, out_loci, details


def run(target_recs, catalog, rules, blastp_exe=None, is_circular=False, workdir=None,
        near_clone_fallback=False, capability=None):
    """Full C5 pass. Never raises on 'no comparators' -- that is a reported state."""
    result = {"state": STATE_NO_COHORT, "nominated": set(), "loci": [],
              "details": {}, "cohort": {}, "presence": {}}
    usable = {pid: recs for pid, recs in (catalog or {}).items()
              if any(r.get("translation") for r in recs)}
    if not usable:
        result["cohort"] = {"considered": 0, "chosen": [],
                            "why": "no comparator genomes were supplied"}
        return result

    # A first pass over EVERY supplied genome, so the cohort can be chosen from
    # measured shared gene content rather than from a filename or a taxonomy guess.
    hits, tmp = orthologs(target_recs, usable, blastp_exe, rules, workdir,
                          capability=capability)
    n_target = sum(1 for r in target_recs if r.get("translation"))
    shared = shared_gene_content(hits, n_target, list(usable))
    cohort, detail = select_cohort(shared, rules, hits=hits,
                                   near_clone_fallback=near_clone_fallback)
    detail["near_clone_fallback_authorised"] = bool(near_clone_fallback)
    result["cohort"] = detail
    result["workdir"] = str(tmp)
    if not cohort:
        detail["why"] = ("no comparator fell inside the shared-gene-content band "
                         "%s" % (detail["band"],))
        return result

    presence = {}
    for r in target_recs:
        per = hits.get(r["cds_id"], {})
        presence[r["cds_id"]] = (sum(1 for pid in cohort if pid in per)
                                 / float(len(cohort)))
    identity = mean_identity(hits, cohort)
    nominated, loci, details = variable_loci(target_recs, presence, rules, is_circular,
                                             identity=identity)
    result.update({"state": STATE_OK, "nominated": nominated, "loci": loci,
                   "details": details, "presence": presence, "identity": identity})
    return result


# --------------------------------------------------------------------------
# catalog loading
# --------------------------------------------------------------------------
_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def load_catalog(path, exclude_phage_id, reader):
    """Read every GenBank in a directory as a comparator proteome.

    `reader` is injected so this module never imports the ingest layer directly and
    stays testable with fixtures. The target genome is excluded by id AND by filename
    so a run cannot compare a genome against itself and call everything conserved.
    """
    root = pathlib.Path(path)
    files = sorted(root.glob("*.genbank")) + sorted(root.glob("*.gbk")) \
        if root.is_dir() else [root]
    catalog = {}
    for f in files:
        pid = f.stem
        if pid == exclude_phage_id or not _SAFE.match(pid):
            continue
        try:
            recs, _ = reader(f, phage_id=pid)
        except Exception:
            continue
        if recs:
            catalog[pid] = recs
    return catalog
