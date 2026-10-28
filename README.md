# DebateFork

A controlled, paired-fork framework for studying how expression cues affect collective judgments and how continued peer interaction changes those effects.

## Current RQ1 protocol

Three same-model agents independently answer S0. Freeze the initial state and one seeded sender, prepare original O plus seven expression variants, then share T1 between the I and M arms. Both continue to T3: I receives no new peer messages after T1; M receives the previous round's peer replies. Each item has 8 materials × 2 terminal arms = 16 cells.

`D = (M_R − M_O) − (I_R − I_O)`

D is calculated per label at the vote and verdict levels. Accuracy and judgment transitions are additional outcomes. Multiple variants share the same O trajectory; the item remains the sampling unit.

## Start here / 使用说明

- [Framework README and full500 command](controlled-mad-framework-v1/README.md)
- [Experiment design and interpretation](controlled-mad-framework-v1/guide/experiment-design.md)
- [Running, ten-item batches, concurrency and resume](controlled-mad-framework-v1/guide/running-experiments.md)
- [Local open-weight inference services and hosted API providers](controlled-mad-framework-v1/guide/model-backends.md)
- [Current versus historical configurations](controlled-mad-framework-v1/configs/README.md)
- [Development and verification](controlled-mad-framework-v1/guide/development.md)
- [Data provenance, licensing and checksums](controlled-mad-framework-v1/datasets/README.md)
- [Changes](controlled-mad-framework-v1/CHANGELOG.md)

Python 3.11+, standard-library runtime, Windows/Linux/macOS. HTTP adapters support OpenAI-compatible Chat Completions and Anthropic-compatible Messages. Local models must be served separately; the framework does not load weights. Local loopback HTTP is supported; external endpoints use HTTPS. Templates require actual model capabilities and credentials before use.

## Verification and study status

As of 2026-10-28, the real MiniMax-M3.1-Flash three-item equal-rounds pilot completed 48/48 cells and 21/21 eligible rewrites. These three items all had unanimous S0 judgments and unchanged terminal labels across conditions; the pilot validates the workflow, not a population effect. New full500 equal-rounds configurations are provided; this does not claim that a new full500 run has finished.

The earlier completed 500-item run used I-T1/M-T3 and remains a separate exploratory protocol. It is not relabeled as an equal-rounds study. RQ2 is a separate five-round MAD supplement on a frozen subset, with T5 final and no I/M four-cell D.

167 automated tests pass, including concurrency/resume equivalence and local HTTP contracts. Mock outputs validate engineering behavior only. Research runs need separately frozen model identities, samples, parameters and budgets.

This repository contains source, templates, synthetic examples and previously published research input datasets with their original license notices. API keys, model weights, private research notes and raw experiment outputs are excluded.
