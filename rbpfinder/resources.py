"""Locate the rule and schema files that ship with the package.

M15A found that a clean install had no way to answer "where are the rules?" -- the CLI
required absolute paths into a checked-out repository, so an installed copy was not
usable on its own.

The files stay physically in the repository's top-level `rules/` directory, which is
still the single source of truth; pyproject maps that directory in as the
`rbpfinder.rules` package, so the installed copy IS the same file rather than a second
one that can drift.
"""
import pathlib

try:
    from importlib import resources as _res
except ImportError:                                   # pragma: no cover
    _res = None

_FALLBACK = pathlib.Path(__file__).resolve().parent.parent / "rules"


def _packaged(name):
    if _res is not None:
        try:
            with _res.as_file(_res.files("rbpfinder.rules") / name) as p:
                if p.exists():
                    return pathlib.Path(p)
        except (ModuleNotFoundError, FileNotFoundError, TypeError, AttributeError):
            pass
    # running from a source checkout that was never installed
    p = _FALLBACK / name
    return p if p.exists() else None


def default_rules_path():
    return _packaged("decision_rules.yaml")


def default_schema_path():
    return _packaged("evidence_schema.json")


def describe():
    """(rules_path, schema_path, provenance) -- used by `rbpfinder doctor`."""
    r, s = default_rules_path(), default_schema_path()
    how = "packaged" if r and "site-packages" in str(r) else "source checkout"
    return r, s, how
