"""Register an existing BLAST database as DB_PHAGE / DB_HOST, and resolve it later.

The cheapest and most reliable way for a lab to get a database is to already have one:
a shared install, a colleague's copy, a previous build. M15C-1 is only about making that
official -- persistent, diagnosable, and separate from any notion of downloading.

Two things this module deliberately does NOT do:

  * copy the database. The configuration records where it is; it never becomes a second
    copy that can drift from the first.
  * re-verify it deeply. Walking 564k records or re-hashing a 176 MB FASTA belongs to the
    build/acceptance chain (`build_blastdb.py`), not to a command a user runs while
    setting up. Configure answers "is this a usable protein BLAST prefix", in milliseconds.

Precedence is frozen, and it is authority rather than a search order -- the same rule as
the executable resolver:

    explicit path given for this run   ->  must be valid, else fail
    RBPFINDER_DB_<ROLE>                ->  must be valid, else fail
    persistent configuration            ->  must be valid, else fail
    otherwise                           ->  not configured

A broken value at a higher level is never quietly replaced by a working one below it. If
someone sets RBPFINDER_DB_PHAGE to a wrong path, they need to hear that, not silently get
the database they configured last month.

Config is a record of intent, never proof of a current fact: `configure` validates at the
time it writes, and every later read re-validates. A database that has since been deleted
or moved reports CONFIGURED, INVALID -- stale config must not impersonate availability.
"""
import datetime
import os
import pathlib

from .errors import InputValidationError

ROLES = ("phage", "host")
ENV_VARS = {"phage": "RBPFINDER_DB_PHAGE", "host": "RBPFINDER_DB_HOST"}

# A protein BLAST database is unusable without all three; .pin alone is a half-built or
# half-copied directory, which is exactly the state worth catching at configure time.
REQUIRED_SIDECARS = (".phr", ".pin", ".psq")

STATUS_AVAILABLE = "AVAILABLE"
STATUS_INVALID = "CONFIGURED, INVALID"
STATUS_NOT_CONFIGURED = "NOT CONFIGURED"


def config_path():
    override = os.environ.get("RBPFINDER_CONFIG")
    if override:
        return pathlib.Path(override)
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME") \
        or os.path.expanduser("~/.config")
    return pathlib.Path(base) / "rbpfinder" / "databases.yaml"


def _load():
    p = config_path()
    if not p.exists():
        return {}
    import yaml
    try:
        # PowerShell and friends write a BOM; the manifest reader learned this the hard
        # way in M15B-3 and there is no reason to relearn it here.
        data = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    except Exception as exc:
        raise InputValidationError("configuration file is unreadable: %s" % exc,
                                   path=str(p))
    return data.get("databases") or {}


def _save(databases):
    import yaml
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"databases": databases}, sort_keys=True,
                                allow_unicode=True), encoding="utf-8")
    return p


def _blastdbcmd_check(prefix):
    """Ask BLAST whether it can open this database. (ok, detail) or None if unavailable.

    Added after a test exposed the sidecar check as insufficient: a copy carrying only
    .phr/.pin/.psq, renamed to a new prefix, passed validation and then failed inside
    blastp with `File ...pdb not found. If you renamed any BLAST database files...`.
    A v5 database needs more sidecars than three, and its index records the ORIGINAL
    name, so file presence alone cannot answer "is this usable".

    This reads the index header only -- milliseconds, no record walking, no hashing. It
    is a structural check performed by the authority on the format, not the deep
    verification that belongs to the build chain. If blastdbcmd is not installed the
    caller falls back and SAYS which check it used.
    """
    from .capability import BlastCapability
    cap = BlastCapability()
    blastp = cap["blastp"]
    if not blastp.available:
        return None
    exe = pathlib.Path(blastp.path).with_name("blastdbcmd.exe")
    if not exe.exists():
        exe = pathlib.Path(blastp.path).with_name("blastdbcmd")
        if not exe.exists():
            return None
    import subprocess
    proc = subprocess.run([str(exe), "-db", str(prefix), "-info"],
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return True, "verified with blastdbcmd -info"
    err = (proc.stderr or proc.stdout or "").strip().splitlines()
    return False, (err[0] if err else "blastdbcmd could not open the database")


def _unfinished_acquisition(base):
    """Is this prefix sitting inside an acquisition that has not been published?

    Caught by M15C-2b gate 13: a run whose build-validation failed had already written
    .pin/.phr/.psq into its staging directory, and on a machine without blastdbcmd the
    structural check could not tell that apart from a finished database. A half-built
    directory must never be able to impersonate an available one -- the same rule as
    "staging is never the download target", applied to reading rather than writing.
    """
    state_file = base.parent / "acquisition_state.json"
    if not state_file.exists():
        return None
    import json
    try:
        state = json.loads(state_file.read_text(encoding="utf-8-sig"))
    except Exception:
        return "an unreadable acquisition state file sits beside it"
    if state.get("state") != "PUBLISHED":
        return ("it is inside an acquisition still in state %s; a staged build is not a "
                "database" % state.get("state"))
    return None


def inspect(prefix):
    """(status, detail, metadata_path) for a candidate BLAST prefix. Never writes."""
    if not prefix:
        return STATUS_NOT_CONFIGURED, "no database configured", None
    base = pathlib.Path(prefix)
    staged = _unfinished_acquisition(base)
    if staged:
        return STATUS_INVALID, staged, None
    missing = [s for s in REQUIRED_SIDECARS if not base.with_name(base.name + s).exists()]
    if missing:
        return (STATUS_INVALID,
                "missing BLAST file%s %s next to %s"
                % ("" if len(missing) == 1 else "s", ", ".join(missing), base.name),
                None)
    checked = _blastdbcmd_check(prefix)
    if checked is not None and not checked[0]:
        return STATUS_INVALID, checked[1], None
    # Say which check was actually performed. A config that implies more verification
    # than happened is the same species of lie as a stale config claiming availability.
    if checked is not None:
        how = checked[1]
    else:
        how = ("sidecar files present; blastdbcmd unavailable, so BLAST itself was "
               "not asked")
    meta = base.with_name(base.name + ".metadata.yaml")
    if meta.exists():
        import yaml
        try:
            yaml.safe_load(meta.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            return (STATUS_INVALID,
                    "metadata file %s exists but cannot be parsed: %s" % (meta.name, exc),
                    str(meta))
        return STATUS_AVAILABLE, "metadata found; %s" % how, str(meta)
    return STATUS_AVAILABLE, "no frozen metadata alongside the database; %s" % how, None


def configure(role, prefix):
    """Validate, then record. Refuses to write anything it could not verify now."""
    if role not in ROLES:
        raise InputValidationError("unknown database role %r (expected %s)"
                                   % (role, " or ".join(ROLES)))
    base = pathlib.Path(prefix)
    if not base.parent.exists():
        raise InputValidationError(
            "no such directory for the database prefix: %s" % base.parent, path=prefix)
    status, detail, meta = inspect(prefix)
    if status != STATUS_AVAILABLE:
        raise InputValidationError("%s is not a usable protein BLAST database: %s"
                                   % (prefix, detail), path=prefix)
    dbs = _load()
    dbs[role] = {
        "prefix": str(base.resolve()),
        "status": "configured",
        "metadata_path": meta,
        "configured_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
    }
    return _save(dbs), dbs[role]


def resolve(role, explicit=None, env=None):
    """(prefix, status, source, detail) under the frozen precedence.

    Every layer re-validates, so a configuration written last month cannot claim a
    database that has since been deleted, and a wrong environment variable is reported
    rather than skipped over.
    """
    env = os.environ if env is None else env
    if explicit:
        status, detail, _ = inspect(explicit)
        return explicit, status, "explicit", detail
    var = ENV_VARS[role]
    if env.get(var):
        status, detail, _ = inspect(env[var])
        return env[var], status, "env", "%s: %s" % (var, detail)
    entry = _load().get(role) or {}
    if entry.get("prefix"):
        status, detail, _ = inspect(entry["prefix"])
        if status == STATUS_INVALID:
            detail = ("configured on %s but no longer usable: %s"
                      % (entry.get("configured_at", "?"), detail))
        return entry["prefix"], status, "config", detail
    return None, STATUS_NOT_CONFIGURED, "unset", \
        "set %s or run: rbpfinder database configure %s <blast-db-prefix>" % (var, role)


def show(env=None):
    return [(role,) + resolve(role, env=env) for role in ROLES]
