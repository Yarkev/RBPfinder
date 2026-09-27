"""M19 -- turning a bare genome FASTA into an annotated GenBank.

Orchestration only. Nothing in this module decides anything scientific: it obtains an
annotation and hands it to the frozen executor, which is unchanged and unaware of where
the GenBank came from. Candidate generation, direction, tiers, Primary/Rescue,
completeness and Stage 6 are not reachable from here.

The problem it solves is a product problem, not a biology one. Before M19, "I have a
FASTA" was answered with a list of things to install -- WSL, Ubuntu, pharokka, phold,
Foldseek. That is a developer toolchain handed to a user. Those tools are now what the
`local` mode needs, and nothing else needs them.
"""
import datetime
import hashlib
import json
import pathlib

from .errors import CapabilityUnavailable, InputValidationError

_BASE = "genome_annotation"


class AnnotationUnavailable(CapabilityUnavailable):
    """The annotation could not be obtained. Without CDS there is nothing to analyse."""


def _now():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def genome_sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def looks_like_bare_fasta(path):
    """Is this a nucleotide FASTA rather than an annotated GenBank?

    Decided by reading the file, not by trusting the extension. A `.fasta` holding a
    GenBank record and a `.gbk` holding a FASTA are both things users actually have, and
    guessing from the suffix would produce a parse error three stages later that looks
    like a corrupt input rather than a wrong mode.
    """
    path = pathlib.Path(path)
    if not path.exists():
        raise InputValidationError("%s does not exist" % path)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(">"):
                return True
            if stripped.startswith("LOCUS"):
                return False
            return False
    raise InputValidationError("%s is empty" % path.name)


def has_cds_features(path):
    """Does this GenBank actually carry CDS features with translations?

    An annotated-looking file with no CDS is the failure this catches: it parses, it has
    a LOCUS line, and it produces an empty analysis that looks like a phage with no
    proteins rather than an input that was never annotated.
    """
    text = pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    return "  CDS  " in text or "\n     CDS " in text


# --------------------------------------------------------------------- the mode choice
def mode_choice_message(rules):
    """What a user sees when they hand over a FASTA without choosing a mode.

    Deliberately not "install Foldseek". The tools are one of three options, and the
    least recommended one.
    """
    modes = rules.get(_BASE + ".modes")
    lines = ["FASTA input requires genome annotation.", "",
             "Choose annotation mode:", ""]
    for i, name in enumerate(("remote", "existing", "local"), start=1):
        summary = modes[name]["summary"]
        lines.append("  %d. %-9s %s" % (i, name, summary))
    lines += ["", "Pass --annotation-mode <remote|existing|local>.",
              "`local` is the only mode that needs locally installed annotation tools."]
    return "\n".join(lines)


def disclosure_notice(rules):
    return rules.get(_BASE + ".disclosure.notice")


def disclosure_prompt(rules):
    return rules.get(_BASE + ".disclosure.prompt")


# ------------------------------------------------------------------------- the session
class AnnotationSession(object):
    """Obtains an annotated GenBank, under an authorisation that is its own.

    The authorisation is deliberately NOT shared with Stage 2. Stage 2 transmits selected
    candidate proteins; annotation transmits the entire genome. Those differ in kind, so
    consent to one is not consent to the other, in either direction.
    """

    def __init__(self, rules, provider, artifact_dir, policy=None, consent=None,
                 requested_mode=None):
        self.rules = rules
        # What the user asked for, kept apart from what happened. A replay satisfies
        # `--annotation-mode remote` without transmitting anything, so recording only
        # "remote" would let a run that never touched the network be read as one that
        # passed the transmission gate.
        self.requested_mode = requested_mode
        self.provider = provider
        self.dir = pathlib.Path(artifact_dir)
        self.policy = policy
        self.consent = consent
        self.disclosure = None
        self.artifacts = {}
        self.limitations = []
        self.submitted_at = None
        self.completed_at = None
        self._gated = False

    # -- policy ------------------------------------------------------------
    def authorise(self):
        """Runs before the genome leaves the machine, never after.

        Order matches the Stage 2 gate for the same reason: consent before policy, so a
        user who has not authorised the run still learns what authorising would mean.
        """
        if self._gated:
            return
        notice = disclosure_notice(self.rules)
        if self.consent is not None and not self.consent(notice):
            raise AnnotationUnavailable(
                "remote annotation was not authorised. The genome has not been sent.",
                capability="genome_annotation")
        if self.policy is not None:
            ok, why = self.policy.allows(self.provider.provider_id)
            if not ok:
                raise AnnotationUnavailable(
                    "remote annotation requested but the data policy forbids it: %s. "
                    "The genome has not been sent." % why,
                    capability="genome_annotation")
        self.disclosure = {
            "shown": notice,
            "statements": self.rules.get(_BASE + ".disclosure.must_state"),
            "scope": "complete_genome",
            "authorised": True,
        }
        self._gated = True

    # -- the work ----------------------------------------------------------
    def annotate(self, genome_fasta, phage_id):
        """Return the path to an annotated GenBank, or raise.

        The contract is narrow on purpose: whatever the provider is, this returns a file
        the existing ingest can read, or it fails. There is no partial success in which
        the run continues without CDS.
        """
        genome_fasta = pathlib.Path(genome_fasta)
        if self.provider.transmits:
            self.authorise()
        self.dir.mkdir(parents=True, exist_ok=True)

        self.submitted_at = _now()
        result = self.provider.annotate(
            genome_fasta=genome_fasta, phage_id=phage_id, out_dir=self.dir)
        self.completed_at = _now()

        gbk = result.get("annotated_genbank")
        if not gbk or not pathlib.Path(gbk).exists():
            raise AnnotationUnavailable(
                "the annotation provider returned no annotated GenBank; without CDS "
                "features there is nothing to analyse", capability="genome_annotation")
        gbk = pathlib.Path(gbk)
        if not has_cds_features(gbk):
            # Parseable but empty of CDS. Left unchecked this becomes a phage that
            # apparently has no proteins, which is a much more expensive lie than a
            # failed run.
            raise AnnotationUnavailable(
                "the returned annotation contains no CDS features; this is a failed "
                "annotation, not a genome without proteins",
                capability="genome_annotation")

        # Optional enrichment. Absent is recorded, never punished: a phold result that
        # never arrived says nothing about any protein.
        for name in self.rules.get(_BASE + ".optional_enrichment.tools"):
            path = result.get(name)
            if path and pathlib.Path(path).exists():
                self.artifacts[name] = str(path)
            else:
                self.artifacts[name] = None
                self.limitations.append({
                    "capability": name,
                    "state": "not_run",
                    "reason": "the annotation provider returned no %s artifact" % name,
                    "effect": "assessment coverage may be reduced",
                    "not_evidence": "a missing artifact is not evidence against any "
                                    "candidate",
                })

        # Three outcomes, and the middle one is why there are three. A missing
        # enrichment artifact is not a failed annotation -- the genes were called, the
        # GenBank is usable, and the run continues with the gap recorded. Collapsing it
        # into either `completed` or `failed` would lose the only fact a reader needs:
        # something optional did not arrive.
        status = ("completed_with_missing_enrichment" if self.limitations
                  else "completed")
        self.result = {
            "annotation_status": status,
            "provider": self.provider.provider_id,
            "input_fasta_sha256": genome_sha256(genome_fasta),
            "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
            "annotated_genbank_path": str(gbk),
            "artifact_paths": dict(self.artifacts),
            # Null when the provider does not expose them. Recorded as null rather than
            # omitted: "the provider did not say" and "we did not ask" are different.
            "tool_versions": (result.get("provenance") or {}).get("tool_versions"),
            "provider_provenance": result.get("provenance", {}),
        }
        return gbk

    def provenance(self):
        return {
            "requested_mode": self.requested_mode,
            "effective_mode": "remote_transmission" if self.provider.transmits
                              else "replay_or_local",
            "genome_transmitted": bool(self.provider.transmits),
            "provider": self.provider.provider_id,
            "transmits": self.provider.transmits,
            # Recorded separately from the Stage 2 disclosure. Two transmissions of
            # different scope, two consents, two records -- collapsing them would make
            # it impossible to say afterwards what the user actually agreed to.
            "disclosure": self.disclosure,
            # `annotation_status` is the headline. A caller that reads only one field
            # should read the right one.
            "annotation_status": (getattr(self, "result", None) or {}).get(
                "annotation_status", "failed"),
            "artifacts": dict(self.artifacts),
            "limitations": self.limitations,
            "result": getattr(self, "result", None),
        }


# ------------------------------------------------------------------------- providers
class ExistingAnnotationProvider(object):
    """Mode `existing`: a GenBank the user already has. Transmits nothing."""

    provider_id = "existing_annotation_import"
    transmits = False

    def __init__(self, genbank_path):
        self.genbank_path = pathlib.Path(genbank_path)

    def annotate(self, genome_fasta, phage_id, out_dir):
        if not self.genbank_path.exists():
            raise InputValidationError(
                "--annotation-genbank %s does not exist" % self.genbank_path)
        return {"annotated_genbank": self.genbank_path,
                "provenance": {"provider": self.provider_id,
                               "source": str(self.genbank_path)}}


class FixtureAnnotationProvider(object):
    """A recorded provider response, replayed. Transmits nothing.

    This is what the M19-a tests run against, and it is labelled `cached` for the same
    reason every other import is: replaying a saved response sends nothing, so it must
    remain usable in `import_only`. It is NOT evidence that any live service works --
    that is M19-b, and until then nothing here may be called network-validated.
    """

    provider_id = "existing_annotation_import"
    transmits = False

    def __init__(self, fixture_dir):
        self.fixture_dir = pathlib.Path(fixture_dir)

    def annotate(self, genome_fasta, phage_id, out_dir):
        manifest = self.fixture_dir / "manifest.json"
        if not manifest.exists():
            raise AnnotationUnavailable(
                "fixture %s has no manifest.json" % self.fixture_dir,
                capability="genome_annotation")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        out = {"provenance": data.get("provenance", {})}
        for key in ("annotated_genbank", "phold", "foldseek"):
            rel = data.get(key)
            out[key] = (self.fixture_dir / rel) if rel else None
        return out
