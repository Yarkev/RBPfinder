# RBPfinder project instructions

Use the installed RBPfinder skill for bacteriophage RBP analysis.
Do not modify RBPfinder scientific rules during routine analysis.

Before each analysis:

- run `rbpfinder doctor`;
- preserve the input file;
- write outputs into a new run directory;
- respect remote-upload disclosure and authorization;
- report Primary, Rescue, and Completeness exactly as produced.

If a command fails, diagnose the capability or input error rather than
changing scientific thresholds.

Scope: tailed dsDNA bacteriophages (Caudoviricetes). Windows is validated;
Linux and macOS are not yet validated.

The detailed workflow lives in the skill
(`.claude/skills/rbpfinder/SKILL.md` and its `references/`).
This file does not duplicate it.
