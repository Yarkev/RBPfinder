# What leaves the machine, and when

**Nothing.** Not by default, and not with any flag.

Every remote submission path was removed from the tool. This page exists so that an agent
reading it stops offering one, and so the reason is on record.

## The refusals

These three are checked **before any ingest**, so a refused run never reaches a transport:

```
--allow-remote                    -> exit 2
--data-policy remote_allowed      -> exit 2
--stage2-backend ebi              -> exit 2
```

`--help` still lists `ebi` as a `--stage2-backend` choice and still documents
`remote_allowed`. Both parse and are then refused. **Do not read the help text as a
capability**; this page and the exit code are authoritative.

The disclosure-and-authorization machinery those flags used to drive is still in the
source (`stage2_remote`, `interpro_remote`, `_remote_consent`) but is unreachable —
`interpro_remote` has no caller at all. Finding that code is not evidence that a remote
route exists.

## The two modes that do work

```
private       nothing is transmitted. Local databases only.  (default)
import_only   no search runs at all; existing results are parsed and replayed.
```

Set with `--data-policy`. The default comes from the packaged rules, not from the
conversation.

`import_only` is the route for evidence a person obtained by hand — an HHpred run, a
Foldseek web search, a BLAST at a server somewhere. RBPfinder parses it with the same
parser and the same evidence builder it would have used itself, so nothing downstream
knows or cares how the file arrived.

## Why remote was removed rather than defaulted off

The decision was the maintainer's, and it was made for the server case: on a machine that
can download Foldseek, a structure database and a BLAST database, **every evidence family
is reachable locally**, so transmitting sequences buys nothing and costs the user their
confidentiality. A flag that is off by default is still a flag someone sets under
deadline pressure; a refused flag is not.

Be straight about what this costs on a desktop, though:

| | Windows / desktop | Linux server |
|---|---|---|
| Family S, sequence homology | needs a local BLAST database the user installs | same |
| Family T, structure | not reachable in-run — no native Foldseek build | local Foldseek + ProstT5 |

So on Windows with no local database, a run honestly reports that it found little, because
little was reachable. That is the intended behaviour and not a bug to route around. The
remedy is to configure a local database, or to use a Linux server, or to bring results in
with `import_only` — never to look for a remote path.

## What an agent should do with this

- Never ask the user for a contact email for a submission. There is no submission.
- Never describe an EBI route as available, slow, or worth trying.
- Before a run, say which families are actually reachable on this machine
  (`rbpfinder doctor` reports it) rather than explaining a thin result afterwards.
- If the user asks for remote search specifically, say it was removed by design, and give
  them the three real options above.
