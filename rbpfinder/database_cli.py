"""`rbpfinder database ...` -- register and inspect existing BLAST databases.

Only `configure` writes, and it writes one small YAML file recording a path. It never
copies a database, never builds one, and never touches the network; acquisition is M15C's
later half and stays a separate command so that a diagnostic or a registration can never
quietly become an installer.
"""
import sys

from . import database
from .errors import InputValidationError, EXIT_OK


def _configure(argv):
    if len(argv) != 2:
        raise InputValidationError(
            "usage: rbpfinder database configure {phage|host} <blast-db-prefix>")
    role, prefix = argv
    path, entry = database.configure(role, prefix)
    print("configured DB_%s" % role.upper())
    print("  prefix    : %s" % entry["prefix"])
    print("  metadata  : %s" % (entry["metadata_path"] or "(none alongside the database)"))
    print("  recorded  : %s" % path)
    print()
    print("The database was NOT copied. This records where it is; every later read "
          "re-validates it.")
    return EXIT_OK


def _show(argv):
    if argv:
        raise InputValidationError("usage: rbpfinder database show")
    print("%-6s %-20s %-9s %s" % ("role", "status", "source", "detail"))
    for role, prefix, status, source, detail in database.show():
        print("%-6s %-20s %-9s %s" % (role, status, source, detail))
        if prefix:
            print("       %s" % prefix)
    return EXIT_OK


def main(argv=None):
    argv = list(sys.argv[2:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: rbpfinder database configure {phage|host} <blast-db-prefix>")
        print("       rbpfinder database show")
        return EXIT_OK
    cmd, rest = argv[0], argv[1:]
    if cmd == "configure":
        return _configure(rest)
    if cmd == "show":
        return _show(rest)
    raise InputValidationError("unknown database command %r (expected configure or show)"
                               % cmd)
