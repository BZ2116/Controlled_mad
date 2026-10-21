# 公开数据与来源

本目录只保存研究输入数据的原始副本。文件内容未修改；下列 SHA-256 可用于核对。模型响应、修辞改写和运行日志不放在这里。

| 数据 | 文件 | SHA-256 | 当前框架状态 |
| --- | --- | --- | --- |
| AVeriTeC dev（500 题） | `averitec/dev.json` | `499793726b4a5406780928a3d9dedc48d6dd53de778f22437d129cacdb08e300` | 已适配；`configs/averitec-*.json` 使用此文件 |
| WiCE claim dev（349 题） | `wice/claim-dev.jsonl` | `67531ca79bde4c81d3752fb69d9cb0d3d6763de5c3028f548b83ef053a6f8042` | 研究候选；尚无本框架适配器或可运行配置 |
| WiCE oracle claim dev | `wice/oracle-claim-dev.jsonl` | `ae33170930d690cb96cd46f097ec5658daaf7fd6700d6d2bf97644e3a8dfb2f9` | 与 WiCE claim dev 配套 |
| WiCE oracle claim test | `wice/oracle-claim-test.jsonl` | `234bb8c2ca4511ca078e49e07c895a2db61a581d03237e7e1ecb7c48c8510a3e` | 来源数据归档，当前实验未使用 |
| WiCE oracle dev 统计 | `wice/oracle-claim-dev-stats.json` | `5234cf363ffb06707b252559f2738c2c40c603efb6162337b5bf6c40375d5b67` | 来源元数据 |

## 来源与许可

- **AVeriTeC**：来自 [AVeriTeC 官方仓库](https://github.com/MichSchli/AVeriTeC) 的 `data/dev.json`。原作者为 Schlichtkrull 等，论文为 *AVeriTeC: A Dataset for Real-world Claim Verification with Evidence from the Web*（NeurIPS 2023 Datasets and Benchmarks）。官方仓库标注 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)；本目录保留原始文件，未改动内容。使用时应注明来源并遵守非商业条款。
- **WiCE**：来自 [WiCE 官方仓库](https://github.com/ryokamoi/wice) 的 claim dev 与 oracle chunks dev/test。原作者为 Kamoi 等，论文为 *WiCE: Real-World Entailment for Claims in Wikipedia*（EMNLP 2023）。按[官方许可说明](https://github.com/ryokamoi/wice/blob/main/LICENSE.md)，注释采用 ODC-BY，底层文本涉及 Wikipedia 的 CC BY-SA 及 Common Crawl 使用条款。本目录中的文件仅改了保存路径和文件名，内容未改动。

MMLU-Pro 和 BBQ 目前只有数据适配器，没有随本项目保存的源数据，因此本次没有上传空壳文件或伪造运行配置。
