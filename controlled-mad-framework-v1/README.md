# DebateFork：受控多智能体辩论实验框架

研究同一条初始意见的表达方式，是否影响集体判断，以及后续同伴互动是否改变这种影响。Python 3.11+，Windows / Linux / macOS，运行时仅依赖标准库。

当前推荐设计为 `study_protocol: continued_peer_exchange_v2`，见[完整研究规范](guide/study-protocol-v2.md)和[人工验证操作](guide/human-validation.md)。主问题是首次接触后的持续互动效应，接受任意结果方向。新版本补齐预选人审、编码、退化推断、论文级多重检验与独立补充对照；旧配置和旧运行保留原行为。

当前 RQ1 使用显式协议 `matrix_protocol: equal_rounds_v10`。每题 8 种材料 × I/M 两个 T3 终局 = **16 格**；一次重复。I/M 共用 T1，之后都走到 T3。I 在 T2/T3 不接收新同伴消息，M 接收上一轮另两人的回复。I 是“首次接触后停止同伴更新”的对照。S0 是讨论前的三人独立初答，另行保留。

2026-10-28：此前 MiniMax-M3.1-Flash 的真实等轮次三题试跑已完成 48/48 格、21/21 份自动合格改写。三题均为 S0 全体一致，终局无表达效应；这验证核心流程，不代表新增v2人工测量已经完成。v2已通过模拟及自动测试，真实v2调用仍需另行启动。此前完成的 500 题使用 I-T1/M-T3 旧协议，保留为独立探索性实验。

- [实验协议、指标和解释边界](guide/experiment-design.md)
- [v2完整研究规范与补充对照](guide/study-protocol-v2.md)
- [材料操纵核查与回复编码](guide/human-validation.md)
- [启动、十题批次、三题并发、暂停和断点续跑](guide/running-experiments.md)
- [更换模型：本地开源服务 / 闭源 API / 参数契约](guide/model-backends.md)
- [配置索引：当前推荐与历史配置](configs/README.md)
- [开发、验证、打包和目录结构](guide/development.md)
- [数据来源、许可与校验和](datasets/README.md)

## 安装与离线演示

从 GitHub 仓库根目录进入框架目录。PowerShell：

```powershell
Set-Location controlled-mad-framework-v1
python -m pip install -e .
python -X utf8 -m controlled_mad validate --config configs/demo.json
python -X utf8 -m controlled_mad init --config configs/demo.json --run-id demo-first --runs-root ../runs
python -X utf8 -m controlled_mad run --run-id demo-first --runs-root ../runs --backend mock
python -X utf8 -m controlled_mad status --run-id demo-first --runs-root ../runs
```

演示使用合成题和模拟回复，无需 API 密钥。首次初始化使用未用过的 run-id。源码直接运行时，可用 `$env:PYTHONPATH = 'src'` 代替安装；Linux/macOS 对应 `export PYTHONPATH=src`。

## MiniMax 完整 500 题

先在本机设置 `MINIMAX_API_KEY`，或在下面命令增加 `--credentials-file <你明确指定的本地文件>`。框架不自动发现 `.env`，配置只记录凭据环境变量名。启动真实模型会产生请求或费用。

```powershell
python -X utf8 -m controlled_mad plan --config configs/averitec-rq1-minimax-equal-rounds-500-v2.json
python -X utf8 -u -m controlled_mad main-run `
  --config configs/averitec-rq1-minimax-equal-rounds-500-v2.json `
  --run-id minimax-continued-peer-500-v2 `
  --runs-root ../runs `
  --question-batch-size 10 `
  --concurrency 3 `
  --item-pipeline
```

同一条 `main-run` 命令再次执行就是续跑。每批十题，最多同时推进三题，题内串行；本批每题十六格完整后才进入下一批。默认有限重试，服务长时间不可用时保留断点退出；额度刷新后再次执行。不要对该协议加 `--single-pass`。

全量目标 500 题、8,000 个终局格、3,500 组 O/R 配对。通常约 66,500 次请求；材料最多三次生成的计划上界 72,500 次，不含网络重试；总调用护栏 90,000 次。**16 是终局格数，不是每题请求数。** 新全量配置启用1999次、seed=20261022的离线bootstrap与Holm校正；退化时p/区间为NA。材料验证预选50题（350对），回复编码预先固定最多600个单位，不自动生成标注。

```powershell
python -X utf8 -m controlled_mad matrix-status --run-id minimax-continued-peer-500-v2 --runs-root ../runs
```

检查应为 500/500 题、8,000/8,000 格；缺材料、缺回复、无多数分别处理，不补造结果。运行报告、逐格 CSV、效应、投票和投递记录写入该 run 的 `reports/` 与 `exports/`。旧协议和新模型分别使用新的 run-id。

## 后续更换模型

实验调度和消息规则保持一致，通过配置选择后端：

| 接入方式 | 配置入口 | 要求 |
| --- | --- | --- |
| 本地开源模型服务 | `configs/averitec-rq1-v10-local-3-v2-template.json` | 先部署 OpenAI 兼容 Chat Completions 服务，允许本机回环 HTTP |
| 服务商 OpenAI 兼容 API | `configs/averitec-rq1-v10-hosted-3-v2-template.json` | 实际 HTTPS 基础地址、模型名、API key 环境变量 |
| Anthropic 兼容 Messages API | 修改 hosted 模板的 provider 和参数 | `provider: anthropic`，请求路径为基础地址加 `/messages` |
| 换模型后的完整实验 | `configs/averitec-rq1-v10-equal-rounds-500-v2-template.json` | 复制三题验证过的模型配置，填写实际容量和预算 |

模板默认网络关闭、模型名待填，不能直接当成可用服务。框架通过 HTTP 调用服务，不直接加载 Transformers 权重，也不负责模型下载、显存调度或启动推理服务器。具体配置和模型返回格式见 [模型接入说明](guide/model-backends.md)。每个新模型重新生成自己的 S0、改写和讨论，不能复用 MiniMax 的回答作为新模型结果。

本地研究记录 `docs/`、运行数据 `runs/`、凭据、模型权重和缓存不发布。公开使用文档在 `guide/`；公开数据按其原始许可使用。
