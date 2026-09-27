"""One definition of subject identity, shared by every sequence-evidence provider.

Found by M16-c C2, 2026-08-25. A `-parse_seqids` local BLAST database returns the full
FASTA id -- `tr|A0AAE7WML0|A0AAE7WML0_9CAUD` -- while EBI returns the bare accession
`A0AAE7WML0`. Both name the same protein. Nothing in the evidence path reconciled them,
so all 54 shared hits of the C2 comparison carried a backend-dependent identity, and the
frozen C1 contract requires subject identity to be equivalent across representations.

This is deliberately NOT a third copy: `benchmark_map` already had this logic for mapping
reference RBPs, and a second private implementation is how two answers to one question
start disagreeing quietly -- the same argument the project makes for keeping one
direction_map.
"""


def subject_identity(raw):
    """`db|ACC|NAME` -> `ACC`. Anything else is returned unchanged.

    Narrow on purpose. It recognises only known identifier namespaces rather than
    stripping at every `|` it can find, because a subject id we do not recognise must
    survive intact -- silently truncating an unfamiliar identifier would be worse than
    leaving it long. Whitelist, never a blacklist (HANDOFF 9.2).
    """
    s = "" if raw is None else str(raw)
    parts = s.split("|")
    if len(parts) >= 3 and parts[0] in ("sp", "tr", "gb", "ref", "emb", "dbj", "pir",
                                        "prf", "pdb", "lcl"):
        return parts[1] or s
    return s
