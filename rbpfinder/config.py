"""Rule access.

Nothing in this file decides biology. It only reads decision_rules.yaml.
Missing keys raise instead of defaulting, so a threshold can never silently
migrate from the YAML into Python.
"""
import pathlib
import yaml

_MISSING = object()


class MissingRule(KeyError):
    """A rule the code needs is absent from decision_rules.yaml."""


class Rules:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self._d = yaml.safe_load(self.path.read_text(encoding="utf-8"))

    def get(self, dotted, default=_MISSING):
        cur = self._d
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                if default is _MISSING:
                    raise MissingRule(
                        "decision_rules.yaml has no key %r "
                        "(add it to the YAML; do not hardcode it)" % dotted
                    )
                return default
            cur = cur[part]
        return cur

    @property
    def raw(self):
        return self._d
