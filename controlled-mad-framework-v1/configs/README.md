# 配置索引

所有配置的相对数据路径以配置文件所在目录解析，不以终端当前目录解析。`validate` / `plan` 离线校验，不调用模型；`main-run` 会根据网络开关与凭据调用模型。

## 当前 RQ1：等轮次 V10、每题 16 格

| 文件 | 作用 | HTTP 调用开关 |
| --- | --- | --- |
| `averitec-rq1-minimax-equal-rounds-3.json` | 已真实验收的 MiniMax 三题配置 | 开 |
| `averitec-rq1-minimax-equal-rounds-500.json` | MiniMax 全量 500 题、一次重复、离线推断 | 开 |
| `averitec-rq1-v10-local-3-template.json` | 本地 OpenAI 兼容服务三题模板 | 关 |
| `averitec-rq1-v10-hosted-3-template.json` | 闭源服务商三题模板 | 关 |
| `averitec-rq1-v10-equal-rounds-3-template.json` | 通用三题容量模板 | 关 |
| `averitec-rq1-v10-equal-rounds-500-template.json` | 通用全量模板、离线推断 | 关 |

共同约定：`complete_matrix: true`、`matrix_layout: group_first_v2`、`matrix_protocol: equal_rounds_v10`、`repeats: 1`、七种操作、严格材料审核。I/M 都到 T3。无额外消息屏蔽或 O/O 诊断。

三题通常 399 次调用，计划上界 435、护栏 600；全量通常 66,500 次，计划上界 72,500、护栏 90,000；均不含网络重试。全量 T2/T3 各 24,000 次自然回复，阶段护栏各 30,000。预算是停止护栏，非精确计费预测。

先复制模板为自己的配置，填写模型名、模型返回名、服务地址、凭据环境变量名、实际上下文/输出上限与解码参数，再将 `network_enabled` 改为 true。本地服务也需要该开关；无鉴权服务仍要设置专用的非秘密占位环境变量，详见 [接入说明](../guide/model-backends.md)。不要把 MiniMax 的 `reasoning_effort: max` 原样复制给其他模型。

## 历史及补充实验

- `averitec-rq1-minimax-full500-matrix-v2.json` 与 `pilot10-matrix-*` 是旧完整矩阵，未指定 `matrix_protocol` 时 I 到 T1、M 到 T3。用于读旧记录或按原协议续跑，不作为新等轮次实验入口。
- `averitec-rq1-minimax-v10-main.json` 等早期配置包含不同重复次数或协议边界，保留兼容，不会自动升级。
- `averitec-rq2-*` / `rq2-five-round-*` 是独立五轮补充实验，通过 `rq2` 命令启动；不追加到已有三轮 run。
- `averitec-rq3-*` 为预定消息屏蔽配置；`v10-replication-matrix.json` 保留跨数据集和模型复核槽位，不表示已经完成复核。
- `demo.json`、`v10-demo.json`、`rq2-five-round-demo.json` 用于 mock 验收，不生成科学实证。

旧配置保持原样，不能在已有 run-id 上更换模型或协议。当前完整矩阵有意限定一次重复；提高重复数、开启 O/O 或消息屏蔽属于独立设计，应使用对应配置与新 run-id。
