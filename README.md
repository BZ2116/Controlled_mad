# DebateFork

**A controlled, paired-fork evaluation framework for studying how expression cues
interact with peer deliberation in multi-agent debate.**

**Status:** The mock workflow runs end to end and is covered by automated tests.
Research runs need a separately frozen model, sample, and budget; mock outputs
are not empirical findings.

---

## Why this framework

A standard "rewrite the input, rerun MAD, compare accuracy" experiment cannot
answer the question we care about:

> When an expression change moves the final verdict, how much of that change
> comes from the first direct reading, and how much only appears after peer
> interaction? Which conclusions depend on missing data, no-majority outcomes,
> or a changed message source?

DebateFork is a measurement protocol that freezes a shared initial state,
branches paired conditions, and records every visible message and round result.

## Core estimator

The three-round protocol compares the expression effect with and without peer
interaction on the same item and initial state:

`D = (M_R - M_O) - (I_R - I_O)`

The separate five-round RQ2 supplement tracks `M_R - M_O` from T1 to T5 on a
preselected subset. T5 is the final verdict. The supplement does not report a
five-round four-cell `D`.

## Repository

- [Framework, commands, and Windows instructions](controlled-mad-framework-v1/README.md)
- [Dataset files, provenance, licenses, and checksums](controlled-mad-framework-v1/datasets/README.md)
- [AVeriTeC and mock configurations](controlled-mad-framework-v1/configs)

AVeriTeC dev is integrated with the framework. WiCE files are included as
research inputs but do not yet have a runnable adapter in this package.
Configuration files contain environment-variable names, not API keys.
