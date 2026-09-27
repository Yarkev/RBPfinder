"""M21-c -- proving a returned result belongs to the sequence it was requested for.

M21-b wrote `candidate_id + sequence_sha256` into every manual batch. That told the user
what they were submitting. This is the other half, at the door.

The failure it stops is ordinary:

    run 1     CDS_0017 = sequence A   ->  HHPRED_BATCH.tsv
    later     the genome is re-annotated, CDS_0017 = sequence B
    run 2     the user hands back the old .hhr

On `candidate_id` alone that result attaches to a different protein and every tier
downstream is about a sequence nobody analysed. A locus tag is a label, and labels get
reused; the hash of the translation is the only thing in the loop that identifies the
object actually analysed.

Two things this module refuses to do.

**It never reads identity out of a result file or its name.** An `.hhr`, an InterPro TSV
and a Foldseek export were written by tools that never heard of this run. The binding
lives in the manifest the user fills in from the batch we gave them, so there is
something to audit afterwards.

**It never turns a failed import into a finding.** A candidate whose result could not be
bound is exactly as unresolved as one whose search never ran. Failing to import is a
statement about the import, not about the protein.
"""
import hashlib

_BASE = "return_binding"

BOUND = "BOUND"
STALE_RESULT = "STALE_RESULT"
UNKNOWN_CANDIDATE = "UNKNOWN_CANDIDATE"
UNBOUND_LEGACY = "UNBOUND_LEGACY"

ACCEPTED = (BOUND, UNBOUND_LEGACY)


class BindingError(Exception):
    """A manifest that cannot be trusted. Never a statement about any protein."""


def sequence_sha256(translation):
    """The one definition. Batch writing and return checking must not each have their own."""
    return hashlib.sha256((translation or "").encode()).hexdigest()


def is_bound_schema(fieldnames):
    """Does this manifest claim to carry bindings?

    Structural rather than a version string: a manifest that HAS the column is bound and
    every row must fill it; one that does not have the column at all is legacy. There is
    no version field to set wrongly and no way to be half-migrated by accident.
    """
    return "sequence_sha256" in (fieldnames or [])


def batch_note(row, issued):
    """What the batch_id says, which is never whether the protein is right.

        hash match + batch match      BOUND
        hash match + batch mismatch   BOUND, with a note
        hash mismatch + batch match   STALE_RESULT, refused

    It answers "did this come back from the batch we think it did", and that is an audit
    question, not an identity one.
    """
    declared = (row.get("batch_id") or "").strip()
    expected = (issued or {}).get(row.get("kind"))
    if not declared or not expected:
        return None
    if declared == expected:
        return None
    return ("batch_mismatch: this row declares batch %s, but the %s batch this run can "
            "see is %s. The sequence still matches, so the result is accepted; it may "
            "have come from an earlier request." % (declared, row.get("kind"), expected))


def classify_row(row, by_id, bound_schema, rules):
    """One manifest row -> (state, detail). Pure; touches no file."""
    cds_id = row.get("cds_id")
    rec = by_id.get(cds_id)
    if not bound_schema:
        return UNBOUND_LEGACY, ("no sequence_sha256 column in this manifest; imported "
                                "without a binding check")
    declared = (row.get("sequence_sha256") or "").strip().lower()
    if not declared:
        # The dangerous middle: a bound manifest with a blank hash looks migrated and is
        # not. It fails here rather than falling through to the legacy path.
        raise BindingError(
            "%s: this manifest has a sequence_sha256 column, so every row must carry a "
            "hash. Row for %r is blank." % (row.get("_source", "manifest"), cds_id))
    if rec is None:
        return UNKNOWN_CANDIDATE, ("no candidate %r in this run; the result cannot be "
                                   "attached to anything" % cds_id)
    actual = sequence_sha256(rec.get("translation"))
    if actual != declared:
        return STALE_RESULT, (
            "the sequence for %s has changed since this result was requested "
            "(manifest %s..., current %s...). The result describes a protein this "
            "candidate no longer has." % (cds_id, declared[:12], actual[:12]))
    return BOUND, "hash matches the current translation of %s" % cds_id


def check(rows, by_id, bound_schema, rules, issued=None):
    """Judge a whole manifest. Returns a verdict dict; raises only on an unusable file.

    `accepted` is what may reach a parser. Everything else is kept, with a reason, so a
    reader can see what was refused and why -- a rejected row that vanished silently
    would be indistinguishable from a row nobody sent.
    """
    verdicts, accepted, rejected = [], [], []
    for row in rows or []:
        state, why = classify_row(row, by_id, bound_schema, rules)
        v = {"cds_id": row.get("cds_id"), "kind": row.get("kind"),
             "path": row.get("path"), "state": state, "reason": why,
             "batch_id": row.get("batch_id") or None,
             # Only meaningful on a row that was accepted. A refused row is refused on
             # the sequence, and adding a batch note to it would suggest the batch had
             # something to do with the refusal.
             "batch_note": (batch_note(row, issued) if state == BOUND else None)}
        verdicts.append(v)
        (accepted if state in ACCEPTED else rejected).append(v)

    reuse = _duplicate_bindings(verdicts, rows, by_id, bound_schema)
    return {
        "schema": "bound" if bound_schema else "legacy",
        "verdicts": verdicts,
        "accepted": accepted,
        "rejected": rejected,
        "counts": {s: sum(1 for v in verdicts if v["state"] == s)
                   for s in (BOUND, STALE_RESULT, UNKNOWN_CANDIDATE, UNBOUND_LEGACY)},
        "same_sequence_reuse": reuse,
        "batch_notes": [{"cds_id": v["cds_id"], "kind": v["kind"], "note": v["batch_note"]}
                        for v in verdicts if v.get("batch_note")],
        # Announced every time. Backward compatibility for a frozen benchmark recording
        # is not a compatibility mode for somebody's new results.
        "legacy_notice": (None if bound_schema else
                          "this manifest carries no sequence_sha256 column, so no result "
                          "was checked against the sequence it was requested for. That is "
                          "supported for frozen recordings and replay; new manual returns "
                          "should use a manifest with the column."),
    }


def _duplicate_bindings(verdicts, rows, by_id, bound_schema):
    """One result file, one input sequence.

    Deduplicating by path would miss this entirely, because the paths are identical --
    that IS the problem. What must be equal is the SEQUENCE behind them.
    """
    if not bound_schema:
        return []
    by_path = {}
    for row in rows or []:
        by_path.setdefault(row.get("path"), []).append(row)
    reuse = []
    for path, group in sorted(by_path.items()):
        if len(group) < 2:
            continue
        hashes = {(r.get("sequence_sha256") or "").strip().lower() for r in group}
        if len(hashes) > 1:
            raise BindingError(
                "%s is bound to %d different sequences (%s). One external analysis "
                "cannot be the result for two different proteins."
                % (path, len(hashes),
                   ", ".join(sorted(h[:12] + "..." for h in hashes))))
        # Same file, same input sequence: two CDS whose translations are byte-identical.
        # Different biological objects, one analysis. Allowed, and said out loud.
        ids = sorted({r.get("cds_id") for r in group})
        translations = {(by_id.get(i) or {}).get("translation") for i in ids}
        if len(translations) == 1 and len(ids) > 1:
            reuse.append({"path": path, "candidate_ids": ids,
                          "same_sequence_reuse": True,
                          "source_candidate_id": ids[0],
                          "note": "identical translations; one analysis, %d candidates"
                                  % len(ids)})
    return reuse


def provenance(verdict, reuse_for=None):
    """What goes into `raw.return_binding` on a record built from an imported file."""
    out = {"state": verdict["state"], "checked": verdict["state"] != UNBOUND_LEGACY,
           "batch_id": verdict.get("batch_id"),
           "batch_note": verdict.get("batch_note"),
           "affects_classification": False}
    if reuse_for:
        out["same_sequence_reuse"] = True
        out["source_candidate_id"] = reuse_for
    return out


def summary_lines(verdict):
    """What stdout and the report both print. Serialised, never re-derived."""
    if not verdict or not verdict["verdicts"]:
        return []
    c = verdict["counts"]
    lines = []
    if verdict["schema"] == "legacy":
        lines.append("    return binding: LEGACY -- %d row(s) imported unchecked"
                     % c[UNBOUND_LEGACY])
        lines.append("      " + verdict["legacy_notice"])
        return lines
    lines.append("    return binding: %d bound, %d stale, %d unknown candidate"
                 % (c[BOUND], c[STALE_RESULT], c[UNKNOWN_CANDIDATE]))
    for v in verdict["rejected"]:
        lines.append("      REFUSED %s (%s): %s" % (v["cds_id"], v["kind"], v["reason"]))
    if verdict["rejected"]:
        lines.append("      A refused import is not a finding. Those candidates carry "
                     "no evidence from it and are unchanged.")
    for n in verdict.get("batch_notes") or []:
        lines.append("      NOTE %s (%s): %s" % (n["cds_id"], n["kind"], n["note"]))
    for r in verdict["same_sequence_reuse"]:
        lines.append("      reused for %s: %s" % (", ".join(r["candidate_ids"]),
                                                  r["note"]))
    return lines


def batch_id(provider, pairs):
    """Which request a result answers. Provenance, never identity.

    Derived from the request CONTENT -- provider plus the candidate/hash pairs it asked
    about -- and deliberately not from a run id or a UUID. A content hash can be checked
    later against the batch that is still on disk; an opaque per-run token can only be
    compared against state somebody remembered to keep.

    It changes when the request changes, and it may never stand in for a matching
    sequence hash: only that can refuse a stale result. See
    `return_binding.batch_id.is_identity`.
    """
    material = "|".join([str(provider)]
                        + ["%s:%s" % (c, h) for c, h in sorted(pairs)])
    return hashlib.sha256(material.encode()).hexdigest()[:16]
