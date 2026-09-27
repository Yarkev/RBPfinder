"""`rbpfinder doctor` -- what can THIS INSTALLATION do?

Not "did my analysis succeed". Doctor answers a question about the machine, and it
answers it in seconds without running anything: it imports, it resolves paths, it stats
files. It never downloads a database, never calls makeblastdb to find out whether C5
would work, and never edits PATH, config or any file. A diagnostic that changes the
thing it is diagnosing is worthless.

Five states, not two. `OK/MISSING` cannot say the difference between a tool nobody
needs and a tool the requested analysis depends on:

    AVAILABLE            present and usable
    MISSING              not present
    NOT CONFIGURED       nothing has told us where it would be
    CONFIGURED, INVALID  we were told where it is, and it is not there
    UNAVAILABLE          a derived capability whose parts are missing

Exit code follows the same product rule as the executor: an OPTIONAL capability that is
absent is not a failure. A user who only runs Mode A needs neither BLAST nor a database,
and telling them their installation is broken would train them to ignore the output.
"""
import os
import pathlib

from . import database, resources
from .capability import BlastCapability
from .errors import EXIT_OK, EXIT_CAPABILITY

DB_ENV = {"DB_PHAGE": "RBPFINDER_DB_PHAGE", "DB_HOST": "RBPFINDER_DB_HOST"}


def _dep(name):
    try:
        import importlib
        importlib.import_module(name)
        return "AVAILABLE", ""
    except ImportError as exc:
        return "MISSING", str(exc)


def _database(role, env):
    """Ask the database module -- doctor must never grow its own second opinion.

    It re-validates on every read, so a configuration whose database has since been
    deleted reports CONFIGURED, INVALID rather than the AVAILABLE it was written as.
    Still shallow and still read-only: no hashing, no record walking, no network.
    """
    _prefix, status, source, detail = database.resolve(role, env=env)
    return status, "%s -- %s" % (source, detail)


def collect(env=None, blastp=None, makeblastdb=None):
    env = os.environ if env is None else env
    rules, schema = resources.default_rules_path(), resources.default_schema_path()
    cap = BlastCapability(blastp=blastp, makeblastdb=makeblastdb, env=env)

    rows = []      # (section, name, state, required_for, detail)
    rows.append(("RBPfinder", "package", "AVAILABLE", "core", ""))
    rows.append(("RBPfinder", "rules", "AVAILABLE" if rules else "MISSING", "core",
                 str(rules) if rules else "decision_rules.yaml not found"))
    rows.append(("RBPfinder", "schema", "AVAILABLE" if schema else "MISSING", "core",
                 str(schema) if schema else "evidence_schema.json not found"))

    for mod, label in (("yaml", "PyYAML"), ("jsonschema", "jsonschema"), ("Bio", "Biopython")):
        state, detail = _dep(mod)
        rows.append(("Core Python", label, state, "core", detail))

    # Executables come from the SAME resolver the run uses, so doctor cannot report a
    # tool the run would then fail to find.
    for name in ("blastp", "makeblastdb"):
        t = cap[name]
        rows.append(("Local executables", name,
                     "AVAILABLE" if t.available else "MISSING",
                     "local_stage2" if name == "blastp" else "c5",
                     "%s -- %s" % (t.source, t.path or t.detail)))
    pharokka = None
    import shutil
    pharokka = shutil.which("pharokka.py", path=env.get("PATH")) or \
        shutil.which("pharokka", path=env.get("PATH"))
    rows.append(("Local executables", "pharokka",
                 "AVAILABLE" if pharokka else "MISSING", "mode_b",
                 pharokka or "optional; Mode A does not need it"))

    dbs = {}
    for label, role in (("DB_PHAGE", "phage"), ("DB_HOST", "host")):
        state, detail = _database(role, env)
        dbs[label] = state
        rows.append(("Databases", label, state, "local_stage2", detail))
        # A host database covers ONE organism, so say which. Doctor reports the scope and
        # stops there: it has no run context and therefore cannot know whether the scope
        # fits the phage a user is about to analyse. The runtime --host gate stays the
        # only authority on applicability; showing the scope here just means the user
        # learns it before the run rather than from a skip reason after it.
        if role == "host" and state == database.STATUS_AVAILABLE:
            from . import stage2_local
            prefix = database.resolve(role, env=env)[0]
            taxon = stage2_local.host_taxon_of(prefix)
            rows.append(("Databases", "", "",
                         "local_stage2",
                         "host scope: %s" % (taxon or
                                             "unknown -- metadata records no host organism")))

    # Derived capabilities. blastp and makeblastdb are reported separately and combined
    # separately: a machine with blastp but no makeblastdb can run Stage 2 and cannot run
    # C5, and one boolean cannot say that.
    have_blastp = cap["blastp"].available
    have_mkdb = cap["makeblastdb"].available
    any_db = any(s == "AVAILABLE" for s in dbs.values())
    caps = [
        ("Mode A GenBank", "AVAILABLE" if rules and schema else "UNAVAILABLE", "core",
         "annotated GenBank in, shortlist out"),
        ("Local Stage 2", "AVAILABLE" if (have_blastp and any_db) else "UNAVAILABLE",
         "local_stage2", "requires blastp + at least one configured database"),
        ("C5 comparative", "AVAILABLE" if (have_blastp and have_mkdb) else "UNAVAILABLE",
         "c5", "requires blastp + makeblastdb + a comparator cohort at run time"),
        ("Mode B FASTA", "AVAILABLE" if pharokka else "UNAVAILABLE", "mode_b",
         "requires pharokka, or a phold table from the cloud route"),
        ("Remote providers", "disabled", "optional",
         "off unless a run passes --allow-remote"),
    ]
    for name, state, req, detail in caps:
        rows.append(("Analysis capabilities", name, state, req, detail))
    return rows, cap


def render(rows):
    out, section = [], None
    for sec, name, state, req, detail in rows:
        if sec != section:
            out.append("")
            out.append(sec)
            section = sec
        tag = "" if req in ("core", "optional") else "  [%s]" % req
        out.append("  %-22s %-20s %s%s" % (name, state, detail, tag))
    return "\n".join(out).strip("\n")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="rbpfinder doctor",
                                 description="report what this installation can do; "
                                             "changes nothing")
    ap.add_argument("--require", action="append", default=[],
                    choices=["local_stage2", "c5", "mode_b"],
                    help="treat this optional capability as required, so its absence "
                         "exits non-zero")
    args = ap.parse_args(argv)

    rows, _cap = collect()
    print(render(rows))

    # ---- evidence capability matrix -----------------------------------------
    # `doctor` inspects an INSTALLATION, so it can answer two of the three axes and
    # must not pretend to answer the third. Reported in the field: users see
    # HHPRED_BATCH.tsv and AF3_MONOMER_BATCH.tsv and conclude those analyses ran.
    # Saying "executor NOT BUNDLED / REQUEST LIST ONLY" here is where that gets
    # corrected -- before a run, not after.
    try:
        from . import capability_matrix as capmat
        from .config import Rules as _Rules
        from . import resources as _res
        rpath = _res.default_rules_path()
        if rpath:
            caps = capmat.build(_Rules(rpath), candidates=None, context={})
            print()
            print("Evidence capabilities (this installation; per-run state is in "
                  "RUN_REPORT.md)")
            group = None
            for row in caps:
                if row["group"] != group:
                    print()
                    print("  " + row["group"])
                    group = row["group"]
                print("    %-24s %-28s can import a result: %s"
                      % (row["label"], row["executor_label"],
                         "yes" if row["result_import"] == "available" else "no"))
            print()
            print("  A batch file is a REQUEST LIST -- work still to be done. Its")
            print("  existence is not evidence that the work happened.")
    except Exception as exc:                       # noqa: BLE001
        # doctor reports; it never fails a machine because one section could not be
        # rendered. But it says so rather than printing nothing.
        print()
        print("  (evidence capability matrix unavailable: %s)" % exc)

    core_missing = [r for r in rows if r[3] == "core" and r[2] != "AVAILABLE"]
    required_missing = [r for r in rows
                        if r[0] == "Analysis capabilities" and r[3] in args.require
                        and r[2] != "AVAILABLE"]
    print()
    if core_missing:
        print("core installation INCOMPLETE: " +
              ", ".join(r[1] for r in core_missing))
        return EXIT_CAPABILITY
    if required_missing:
        print("required capability unavailable: " +
              ", ".join(r[1] for r in required_missing))
        return EXIT_CAPABILITY
    optional_missing = [r[1] for r in rows
                        if r[2] in ("MISSING", "UNAVAILABLE", "NOT CONFIGURED",
                                    "CONFIGURED, INVALID") and r[3] != "core"]
    print("core installation OK" +
          ("; optional not available: " + ", ".join(sorted(set(optional_missing)))
           if optional_missing else ""))
    return EXIT_OK
