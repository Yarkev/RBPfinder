"""Data policy -- decided once per run, obeyed by every stage.

The user answers one question: may my sequences leave this machine? They should
not have to know that HHpred's web server, EBI BLAST, InterProScan online,
Foldseek Web and the AlphaFold Server all upload the sequence while a local
BLAST database and an existing .hhr file do not. That knowledge lives in the
provider registry in decision_rules.yaml; this class applies it.
"""

PRIVATE = "private"
REMOTE_ALLOWED = "remote_allowed"
IMPORT_ONLY = "import_only"


class PolicyViolation(Exception):
    pass


class DataPolicy:
    def __init__(self, rules, mode=None, run_authorised=False):
        self.rules = rules
        self.mode = mode or rules.get("data_policy.default")
        modes = rules.get("data_policy.modes")
        if self.mode not in modes:
            raise PolicyViolation(
                "unknown data policy mode %r; expected one of %s"
                % (self.mode, ", ".join(sorted(modes))))
        self.spec = modes[self.mode]
        self.run_authorised = bool(run_authorised)
        self.registry = {p["id"]: p for p in rules.get("data_policy.provider_registry")}
        self.blocked = []

        if (self.mode == REMOTE_ALLOWED and not self.run_authorised):
            # the mode alone is not consent; the run must carry it
            self.effective_remote = False
            self.blocked.append(
                "remote_allowed was selected but this run carries no authorisation")
        else:
            self.effective_remote = (self.mode == REMOTE_ALLOWED)

    # -- queries ------------------------------------------------------------
    def transmits(self, provider_id):
        p = self.registry.get(provider_id)
        return None if p is None else bool(p["transmits"])

    def allows(self, provider_id):
        """Return (allowed, reason). An unregistered provider is refused."""
        p = self.registry.get(provider_id)
        if p is None:
            return False, ("provider %r is not in data_policy.provider_registry, so its "
                           "transmission behaviour is unknown" % provider_id)
        if p["transmits"]:
            if not self.effective_remote:
                return False, ("%s transmits the sequence off this machine; mode %s "
                               "forbids that" % (provider_id, self.mode))
            return True, "remote permitted for this run"
        if p["kind"] == "local_search" and self.spec.get("local_search") == "forbidden":
            return False, "%s runs a search; mode %s parses existing results only" % (
                provider_id, self.mode)
        return True, "no transmission"

    # -- reporting ----------------------------------------------------------
    def run_flags(self):
        flags = ["DATA_POLICY_%s" % self.mode.upper()]
        if self.effective_remote:
            flags.append("REMOTE_SUBMISSION_ENABLED_BY_OPERATOR")
        else:
            flags.append("REMOTE_SUBMISSION_BLOCKED")
        return flags

    def summary_lines(self):
        remote = ("permitted for this run" if self.effective_remote
                  else "blocked -- nothing is sent to a third party")
        local = ("blocked (import-only)"
                 if self.spec.get("local_search") == "forbidden" else "used when available")
        vals = {
            "mode": self.mode,
            "remote": remote,
            "local": local,
            "on_missing": self.spec.get("on_missing",
                                        "recorded as not_run and reported"),
        }
        return [t.format(**vals)
                for t in self.rules.get("data_policy.user_facing_summary")]
