"""M19-c -- an append-only progress stream for long remote runs.

The field report described a run where nothing was printed for ten minutes and the user
resorted to counting files in `stage2_remote/` from a second terminal to see whether the
process was still alive. This makes progress observable without putting a single EBI
`/status` response in front of a person or a language model.

**This file is a DISPLAY layer. It is not a recovery source.**

That is the load-bearing constraint, not a caveat. Reading these events back to decide
"this search already completed, skip it" would make a truncated or stale log silently
skip real work -- and a log written for humans is exactly the artefact most likely to be
truncated, hand-edited or copied between directories. Recovery gets its own persistent
state in M19-d, keyed on SearchKey, written transactionally. Until then, an interrupted
run re-submits, and that is the honest behaviour.

Also deliberately absent from every event: sequences, email addresses, and raw provider
payloads. A progress log tends to be pasted into issue trackers and chat windows.
"""
import hashlib
import json
import pathlib
import time

# Read by the gate in scripts/test_stage2_events.py. Stated as data rather than only in
# prose so a future edit that starts using this for recovery fails a test rather than a
# code review.
IS_RECOVERY_SOURCE = False
RECOVERY_SOURCE_IS = "M19-d persistent search state (not yet implemented)"

# Fields that must never appear in an event, at any nesting depth.
FORBIDDEN_KEYS = ("translation", "sequence", "email", "payload", "raw", "hits",
                  "hit_hsps", "query", "protein")

SUBMITTED = "submitted"
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
SKIPPED = "skipped"

# Result values on a `completed` event. These are the epistemic states the project has
# repeatedly had to defend; the progress layer must not blur them back together.
SEARCHED_HITS = "searched_hits"
SEARCHED_NO_HIT = "searched_no_hit"


def display_key(state_key):
    """The short, anonymous form shown in the progress log.

    DERIVED from the recovery identity rather than computed independently. The first
    version minted its own hash, so the same search had two identities -- the state was
    written under one and looked up under the other, and every restart re-submitted work
    it had already completed. One identity, one presentation.
    """
    return "sk_" + str(state_key)[:16]


def search_key(cds_id, taxonomy, sequence_sha256):
    """Convenience: the display form, straight from the three inputs.

    Kept in step with `search_state.search_key` by construction, not by agreement --
    it calls it.
    """
    from .search_state import search_key as _identity
    return display_key(_identity(cds_id, taxonomy, sequence_sha256))


class EventLog(object):
    """Append-only JSONL. Every line is one immutable fact, flushed as it is written."""

    def __init__(self, path, total=0, heartbeat_seconds=30):
        self.path = pathlib.Path(path)
        self.total = total
        self.heartbeat_seconds = heartbeat_seconds
        self.counts = {"completed_hits": 0, "completed_no_hit": 0,
                       "failed": 0, "submitted": 0}
        self._last_heartbeat = {}
        self._done = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # -- writing -----------------------------------------------------------
    def _scrub(self, event):
        """Refuse to write anything on the forbidden list, however it got in."""
        clean = {}
        for k, v in event.items():
            if any(bad in k.lower() for bad in FORBIDDEN_KEYS):
                continue
            if isinstance(v, dict):
                v = self._scrub(v)
            clean[k] = v
        return clean

    def _append(self, event):
        event = self._scrub(event)
        event.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
        event["done"] = self._done
        event["total"] = self.total
        # Opened, written and closed per line. A long run must not lose its last minutes
        # of progress to a buffer that never flushed, and that is precisely the run this
        # exists for.
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
            fh.flush()
        return event

    def submitted(self, key, job_id=None, taxonomy=None):
        self.counts["submitted"] += 1
        self._last_heartbeat[key] = time.time()
        return self._append({"event": SUBMITTED, "search_key": key,
                             "job_id": job_id, "taxonomy": taxonomy})

    def running(self, key, elapsed_s):
        """Throttled heartbeat. An unthrottled one turns the log into the noise it
        was meant to replace."""
        now = time.time()
        last = self._last_heartbeat.get(key, 0)
        if now - last < self.heartbeat_seconds:
            return None
        self._last_heartbeat[key] = now
        return self._append({"event": RUNNING, "search_key": key,
                             "elapsed_s": int(elapsed_s)})

    def completed(self, key, result, taxonomy=None):
        self._done += 1
        if result == SEARCHED_HITS:
            self.counts["completed_hits"] += 1
        else:
            self.counts["completed_no_hit"] += 1
        return self._append({"event": COMPLETED, "search_key": key,
                             "result": result, "taxonomy": taxonomy})

    def failed(self, key, error_class, taxonomy=None):
        self._done += 1
        self.counts["failed"] += 1
        # `failed` and `searched_no_hit` are separate events, never one "no result".
        # Collapsing them is the exact confusion that made a run with 40 transport
        # failures read as a run that searched and found nothing.
        return self._append({"event": FAILED, "search_key": key,
                             "error_class": error_class, "taxonomy": taxonomy})

    # -- reading, for display only ------------------------------------------
    def summary(self):
        """The compact state an agent should read INSTEAD of the raw responses."""
        pending = max(self.total - self._done, 0)
        return {"total": self.total,
                "completed_hits": self.counts["completed_hits"],
                "completed_no_hit": self.counts["completed_no_hit"],
                "failed": self.counts["failed"],
                "pending": pending}

    def progress_line(self):
        s = self.summary()
        return ("Stage 2: %d completed / %d no-hit / %d failed / %d pending"
                % (s["completed_hits"], s["completed_no_hit"], s["failed"],
                   s["pending"]))


def read_summary(path):
    """Re-derive the summary from a log file, for display after the fact.

    Tolerates a truncated final line, because an interrupted run leaves one. It returns
    a `complete` flag saying so -- and callers must not use this for recovery decisions
    whatever that flag says. See IS_RECOVERY_SOURCE.
    """
    path = pathlib.Path(path)
    counts = {"completed_hits": 0, "completed_no_hit": 0, "failed": 0}
    total, done, truncated = 0, 0, False
    if not path.exists():
        return {"total": 0, "completed_hits": 0, "completed_no_hit": 0,
                "failed": 0, "pending": 0, "complete": False,
                "usable_for_recovery": False}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            truncated = True
            continue
        total = max(total, ev.get("total") or 0)
        if ev.get("event") == COMPLETED:
            done += 1
            key = ("completed_hits" if ev.get("result") == SEARCHED_HITS
                   else "completed_no_hit")
            counts[key] += 1
        elif ev.get("event") == FAILED:
            done += 1
            counts["failed"] += 1
    out = dict(counts)
    out.update({"total": total, "pending": max(total - done, 0),
                "complete": not truncated,
                # Stated on every read, so a caller cannot claim it did not know.
                "usable_for_recovery": False})
    return out
