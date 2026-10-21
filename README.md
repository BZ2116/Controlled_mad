# DebateFork

**A controlled, paired-fork evaluation framework for studying how expression cues
interact with peer deliberation in multi-agent debate.**

> ⚠️ **Status: research prototype.** The current implementation is an offline
> prototype. It has passed syntax checks but has **not** yet been validated
> end-to-end, and no results reported here should be treated as empirical
> findings. See [Known Gaps](#known-gaps).

---

## Why this framework

A standard "rewrite the input, rerun MAD, compare accuracy" experiment cannot
answer the question we care about:

> When an expression change moves the final verdict, how much of that change
> comes from the first direct reading, and how much only appears after peer
> interaction? Which conclusions depend on missing data, no-majority outcomes,
> or a changed message source?

DebateFork is not a new MAD reasoning algorithm. It is a **measurement protocol**
that makes those comparisons identifiable.

## Core estimator

Within-item paired interaction difference:
