# DebateFork

A controlled, paired-fork framework for studying how expression cues affect collective judgments and how continued peer interaction changes those effects.

## Current RQ1 protocol: continued peer exchange v2

Three same-model agents independently answer S0. Freeze the initial state and one seeded sender, prepare original O plus seven expression variants, then share T1 between the I and M arms. Both continue to T3: I receives no new peer messages after T1; M receives the previous round's peer replies. Each item has 8 materials × 2 terminal arms = 16 cells.

`D = (M_R − M_O) − (I_R − I_O)`

D is calculated per label at the vote and verdict levels. Accuracy and judgment transitions are additional outcomes. Multiple variants share the same O trajectory; the item remains the sampling unit.

Study v2 adds preselected human manipulation checks (50 items / up to350 material pairs), pre-output stratified reply coding (up to600 units), 1999 item-bootstrap resamples / seed20261022, conservative handling of degenerate inference, explicit missing denominators, and separate neutral/O-O and directed-message controls. It does not assume MAD must improve accuracy or amplify expression effects. The current v2 protocol has passed a real three-item MiniMax pilot. Human annotation remains outstanding; automated acceptance is not a semantic-equivalence proof.

- [Real v2 pilot verification and limits](controlled-mad-framework-v1/guide/pilot-validation-v2.md)
- [Study protocol v2](controlled-mad-framework-v1/guide/study-protocol-v2.md)
- [Human validation, reply coding and paper-wide inference](controlled-mad-framework-v1/guide/human-validation.md)

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

As of 2026-10-30, the current study-v2 configuration completed a real MiniMax-M3.1-Flash pilot: **3/3 items, 48/48 terminal cells and 21/21 automatically eligible rewrites**. Checks passed for 24 shared T1 snapshots, 63 initial recipient-input comparisons, 288 retained-history prefixes and all 360 physical debate reply routes. Re-running the same command issued zero new requests.

There were 401 completed tasks and 412 HTTP attempts, including 11 recovered HTTP 429 responses. One rejected pathos rewrite was regenerated and audited before use. The local Ollama/Qwen3 4B attempt did not complete acceptance on the tested 4GB GPU; MiniMax was run separately as the fallback. This does not claim general Ollama compatibility or incompatibility.

Terminal labels were unchanged across conditions in these three items; the pilot validates the workflow, not a population effect. Human material validation and reply coding are still outstanding. New full500 equal-rounds configurations are provided; this does not claim that a new full500 run has finished.

The earlier completed 500-item run used I-T1/M-T3 and remains a separate exploratory protocol. It is not relabeled as an equal-rounds study. RQ2 is a separate five-round MAD supplement on a frozen subset, with T5 final and no I/M four-cell D.

181 automated tests pass, including concurrency/resume equivalence and local HTTP contracts. A separate known-nonzero/reversal mock fixture passed, including structural-zero T1 and zero-request resume. Mock outputs validate engineering behavior only. Research runs need separately frozen model identities, samples, parameters and budgets.

This repository contains source, templates, synthetic examples and previously published research input datasets with their original license notices. API keys, model weights, private research notes and raw experiment outputs are excluded.
