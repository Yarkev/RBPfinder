"""Run Stage 2 from the databases this installation already has configured.

M15D found the gap that made this necessary: `doctor` reported `Local Stage 2 AVAILABLE`
with both databases registered, and the one formal command still ran only G+D. Every
number in the product benchmark came from a Stage 2 run that a user could not reach
without hand-building a manifest with a developer script. The capability matrix
described something the run did not do.

Two rules shape this module.

**Configured does not mean applicable.** DB_PHAGE is safe to use for any tailed phage --
that is what it is. DB_HOST is not: it is one bacterial genus. Searching a
Stenotrophomonas host database for a Klebsiella phage would not merely be useless, it
would attach host-context evidence drawn from the wrong organism. So DB_HOST is used
only when the run states a host that matches it, and otherwise is skipped WITH THE
REASON RECORDED -- never silently, and never by guessing.

**Automatic mode ends up in the manifest contract it would have used anyway.** The
databases are searched, per-CDS outfmt-6 artefacts land on disk, a run-local manifest
indexes them, and the ordinary local-files provider consumes it. Nothing here builds an
EvidenceRecord. That keeps one set of evidence semantics for the automatic route and the
frozen benchmark route, keeps `searched-no-hit` expressed as a 0-byte artefact, and
leaves the user a manifest they can replay offline.
"""
import csv
import pathlib
import re

from . import database
from .parse import blast_tabular
from .providers import local_files
from .providers.local_blast import LocalBlast

_BASE = "stage2.local_database_use"


def _metadata_text(prefix):
    p = pathlib.Path(str(prefix) + ".metadata.yaml")
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def host_taxon_of(prefix):
    """The organism a host database actually covers, from its frozen metadata."""
    text = _metadata_text(prefix)
    m = re.search(r"database_name:\s*\"?DB_HOST\s*=\s*([A-Za-z][A-Za-z0-9_. -]*?)\s*"
                  r"(?:genus|species|group)?\s*UniProtKB", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"taxonomy_id[:\s]+(\d+)", text)
    return ("taxonomy_id:" + m.group(1)) if m else None


def host_matches(db_host_taxon, run_host_context):
    """Genus-level containment, both directions, case-insensitive.

    Deliberately conservative: 'Stenotrophomonas maltophilia' matches a Stenotrophomonas
    database, 'Klebsiella pneumoniae' does not. Anything it cannot decide counts as a
    mismatch, because the cost of using the wrong host database is a scientific error,
    while the cost of skipping it is a recorded limitation.
    """
    if not db_host_taxon or not run_host_context:
        return False
    a = db_host_taxon.strip().lower()
    b = run_host_context.strip().lower()
    if a.startswith("taxonomy_id:") or b.startswith("taxonomy_id:"):
        return a == b
    ga, gb = a.split()[0], b.split()[0]
    return ga == gb


def resolve_databases(rules, host_context=None, env=None, explicit=None):
    """Which local databases apply to THIS run, and why the others do not."""
    decisions = []
    use = []
    for role in ("phage", "host"):
        prefix, status, source, detail = database.resolve(
            role, explicit=(explicit or {}).get(role), env=env)
        if status == database.STATUS_NOT_CONFIGURED:
            decisions.append({"role": role, "used": False,
                              "reason": "not_configured", "detail": detail})
            continue
        if status != database.STATUS_AVAILABLE:
            # Configured but broken is never silently skipped: the user told us it was
            # there. Surfacing it is the same rule the BLAST resolver follows.
            decisions.append({"role": role, "used": False, "reason": "configured_invalid",
                              "detail": detail, "prefix": prefix, "fatal": True})
            continue
        if role == "host":
            taxon = host_taxon_of(prefix)
            if not host_context:
                decisions.append({"role": role, "used": False, "prefix": prefix,
                                  "reason": "skipped_due_to_host_context",
                                  "detail": "the run states no host; DB_HOST covers %s"
                                            % (taxon or "an unrecorded organism")})
                continue
            if not host_matches(taxon, host_context):
                decisions.append({"role": role, "used": False, "prefix": prefix,
                                  "reason": "skipped_due_to_host_context",
                                  "detail": "DB_HOST covers %s; this run's host is %s"
                                            % (taxon, host_context)})
                continue
            decisions.append({"role": role, "used": True, "prefix": prefix,
                              "reason": "host_context_matches", "detail":
                              "DB_HOST covers %s" % taxon, "source": source})
            use.append((role, prefix))
            continue
        decisions.append({"role": role, "used": True, "prefix": prefix,
                          "reason": "applies_to_any_tailed_phage", "detail": detail,
                          "source": source})
        use.append((role, prefix))
    return use, decisions


def prepare(cds_rows, rules, capability, outdir, host_context=None, env=None,
            explicit=None):
    """Search the applicable databases and return (manifest_path, provenance).

    Returns (None, provenance) when no database applies -- Stage 2 simply does not run,
    the core analysis still completes, and the provenance says why.
    """
    use, decisions = resolve_databases(rules, host_context=host_context, env=env,
                                       explicit=explicit)
    prov = {"decisions": decisions, "databases_used": [r for r, _ in use],
            "manifest": None, "artifact_dir": None}
    fatal = [d for d in decisions if d.get("fatal")]
    if fatal:
        from .errors import InputValidationError
        raise InputValidationError(
            "database configured for role '%s' is not usable: %s"
            % (fatal[0]["role"], fatal[0]["detail"]))
    if not use:
        prov["reason"] = "no applicable local database; Stage 2 did not run"
        return None, prov

    recs = [r for r in cds_rows if r.get("translation")]
    art_dir = pathlib.Path(outdir) / "stage2_local"
    art_dir.mkdir(parents=True, exist_ok=True)

    per_db = []
    for role, prefix in use:
        lb = LocalBlast(db_path=prefix, capability=capability,
                        cache_dir=str(art_dir / ("cache_" + role)))
        per_db.append(lb.search_many(recs))

    rows = []
    for rec in recs:
        cid = rec["cds_id"]
        # One S unit no matter how many databases were consulted -- the de-correlation
        # rule, applied before any record is built, exactly as the benchmark route does.
        hits = local_files._merge_sequence_hits(*[d.get(cid, []) for d in per_db])
        path = art_dir / (cid + ".tsv")
        # An empty file is written on purpose: "searched and found nothing" and "never
        # searched" must stay distinguishable on disk.
        blast_tabular.write(path, {cid: hits})
        rows.append({"cds_id": cid, "kind": "local_blast_tsv", "path": str(path)})

    manifest = art_dir / "stage2_manifest.tsv"
    with manifest.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["cds_id", "kind", "path"], delimiter="\t")
        w.writeheader()
        w.writerows(rows)
    prov.update({"manifest": str(manifest), "artifact_dir": str(art_dir),
                 "cds_searched": len(rows),
                 "cds_with_hits": sum(1 for r in rows
                                      if pathlib.Path(r["path"]).stat().st_size > 0)})
    return manifest, prov
