# What leaves the machine, and when

RBPfinder decides this once per run, before anything is transmitted. The default is that
**nothing leaves**.

## The three modes

```
private          nothing is transmitted. Local databases only.
remote_allowed   transmission is permitted, but still requires per-run authorization.
import_only      no search runs at all; existing results are parsed and replayed.
```

Set with `--data-policy`. The default comes from the packaged rules, not from the
conversation.

## `remote_allowed` is not sufficient on its own

A run that may transmit still has to be authorized:

```
--data-policy remote_allowed     the mode permits it
--allow-remote                   this particular run is authorized
```

Without `--allow-remote`, nothing is sent. RBPfinder prints the disclosure and refuses.

## What remote Stage 2 actually sends

```
Remote Stage 2 uses EMBL-EBI.

The following data will be transmitted:
- candidate protein sequences
- contact email required for EBI job submission

RBPfinder will retrieve and parse the BLAST results automatically.
You will not need to return any result files manually.
```

That notice is printed before the first sequence leaves, **including** when the run is
authorized by `--allow-remote` — the flag authorizes, it does not silence. What was
disclosed is also recorded in `run_result.json` under `stage2_remote.disclosure`, so a
consent that happened can still be shown afterwards.

## The email

```
EBI contact email
Used only as contact information for the EMBL-EBI Job Dispatcher.
RBPfinder retrieves results automatically.
```

It is **not** an RBPfinder account and results are **not** delivered to it. The service
requires a contact address for jobs submitted to it; that is its entire purpose here.

RBPfinder will not invent one, and neither should an agent. Use a real address the user
controls — a lab or project address is fine, `@localhost` and made-up addresses are not.

## Refusals, and when they happen

All three happen **before transmission**, with zero submissions attempted:

```
backend=ebi + no email            -> exit 2, nothing sent
backend=ebi + no authorization    -> exit 3, nothing sent
data policy forbids transmission  -> exit 3, nothing sent
```

## Choosing a profile

| Profile | Needs | Sequences leave the machine? |
|---|---|---|
| `remote` (default for new installs) | RBPfinder + Python dependencies | yes, to EMBL-EBI, with disclosure and authorization |
| `local` | RBPfinder + BLAST+ + the sequence databases | no |

Choose `local` when the sequences are unpublished, confidential, or under an agreement
that forbids third-party submission. That is the case `private` mode exists for, and it is
the user's call, not an agent's.
