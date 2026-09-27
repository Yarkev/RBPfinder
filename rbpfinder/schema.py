"""JSON-Schema validation used as a hard gate before any report is written."""
import json
import pathlib

import jsonschema


class SchemaViolation(Exception):
    def __init__(self, errors):
        self.errors = errors
        super().__init__("run_result failed schema validation (%d errors)" % len(errors))


def load(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def validate(run_result, schema):
    """Return a sorted list of human-readable errors. Empty list means valid."""
    validator = jsonschema.Draft202012Validator(schema)
    out = []
    for err in validator.iter_errors(run_result):
        loc = "$" + "".join("[%r]" % p for p in err.absolute_path)
        out.append("%s: %s" % (loc, err.message))
    return sorted(out)


def require_valid(run_result, schema):
    errs = validate(run_result, schema)
    if errs:
        raise SchemaViolation(errs)
