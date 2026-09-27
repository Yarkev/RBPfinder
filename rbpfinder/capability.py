"""One resolver for the external executables, shared by every caller.

M15A found a bare `FileNotFoundError` from deep inside `subprocess` when blastp was
absent, and M15B-1 found something worse: a run in a pristine virtualenv PASSED, because
a hard-coded default pointed at the developer's own BLAST install and that machine
happened to have it. The venv was clean; the machine was not.

So two rules shape this module.

**Precedence is authority, not a search order.** If you name a path explicitly, or set
the environment variable, that IS the answer -- if it is wrong, the run fails there. It
must never quietly slide down to whatever `PATH` happens to offer, because that is
exactly how a misconfigured machine produces a green test.

**Each executable resolves independently, and callers declare what they need.** There is
no single `blast_available` boolean, because the requirements differ: searching an
existing database needs `blastp` alone, while C5 builds a cohort database and needs
`makeblastdb` too. A machine with blastp but no makeblastdb must be able to run Stage 2
and report C5 as unavailable -- one boolean cannot express that.

Nothing here swallows an error and returns None. A missing capability raises, so callers
cannot each invent their own way of failing.
"""
import os
import pathlib
import shutil

from .errors import CapabilityUnavailable

ENV_VARS = {"blastp": "RBPFINDER_BLASTP", "makeblastdb": "RBPFINDER_MAKEBLASTDB"}


class Tool(object):
    """One resolved executable, with where the answer came from."""

    def __init__(self, name, path=None, source="unavailable", detail=""):
        self.name = name
        self.path = str(path) if path else None
        self.source = source          # explicit | env | path | unavailable
        self.detail = detail

    @property
    def available(self):
        return self.path is not None

    def __repr__(self):                                   # pragma: no cover
        return "<Tool %s %s (%s)>" % (self.name, self.path or "MISSING", self.source)


def _usable(p, search_path=None):
    if not p:
        return None
    q = pathlib.Path(p)
    if q.is_file():
        return q
    # a bare name given explicitly still has to resolve to something real
    w = shutil.which(str(p), path=search_path)
    return pathlib.Path(w) if w else None


def _resolve(name, explicit, env):
    # PATH comes from the SAME env mapping, so an injected environment is honoured in
    # full. Reading os.environ here while accepting an `env` argument would make every
    # test that injects an environment quietly lie.
    search_path = env.get("PATH")
    if explicit:
        got = _usable(explicit, search_path)
        if got:
            return Tool(name, got, "explicit", "given on the command line")
        # authoritative failure: do NOT fall through to env or PATH
        return Tool(name, None, "unavailable",
                    "the path given for %s does not exist: %s" % (name, explicit))

    var = ENV_VARS[name]
    if env.get(var):
        got = _usable(env[var], search_path)
        if got:
            return Tool(name, got, "env", "from %s" % var)
        return Tool(name, None, "unavailable",
                    "%s is set but does not point at an executable: %s" % (var, env[var]))

    found = shutil.which(name, path=search_path)
    if found:
        return Tool(name, found, "path", "found on PATH")
    return Tool(name, None, "unavailable",
                "%s was not given, %s is unset, and it is not on PATH" % (name, var))


class BlastCapability(object):
    def __init__(self, blastp=None, makeblastdb=None, env=None):
        env = os.environ if env is None else env
        self.tools = {
            "blastp": _resolve("blastp", blastp, env),
            "makeblastdb": _resolve("makeblastdb", makeblastdb, env),
        }

    def __getitem__(self, name):
        return self.tools[name]

    def get(self, name):
        return self.tools[name]

    def require(self, *names):
        """Return {name: path}, or raise naming exactly which executable is missing."""
        missing = [self.tools[n] for n in names if not self.tools[n].available]
        if missing:
            raise CapabilityUnavailable(
                "; ".join("%s unavailable -- %s" % (t.name, t.detail) for t in missing),
                capability=",".join(t.name for t in missing))
        return {n: self.tools[n].path for n in names}

    def describe(self):
        """Rows for `rbpfinder doctor` and for run provenance."""
        return {n: {"path": t.path, "source": t.source, "detail": t.detail}
                for n, t in sorted(self.tools.items())}
