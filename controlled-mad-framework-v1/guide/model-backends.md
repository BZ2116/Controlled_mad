# 模型接入：本地与服务商

实验框架只向模型服务发送标准 HTTP 请求。开源/闭源是模型来源；实际后端按服务暴露的接口选择。本地开源模型与闭源服务商只要满足同一个接口契约，就沿用相同实验调度和消息历史。

## 支持的接口

| profile.provider | 实际请求 | 最终文本与终止字段 |
| --- | --- | --- |
| `openai` | `base_url` + `/chat/completions` | 单一 `choices[0].message.content`；`finish_reason: stop` |
| `anthropic` | `base_url` + `/messages` | `content` 中 text 块；`stop_reason: end_turn` 或 `stop_sequence` |
| `mock` | 内置模拟场景，不发 HTTP | 只作工程验收 |

基础地址通常包含 `/v1`，不要把完整 `/chat/completions` 或 `/messages` 填进去。请求不流式、无工具调用、无多候选输出。模型需遵循冻结提示返回相应 JSON；S0/讨论要求 label 和 comment，材料/审核有各自结构。reasoning 字段不能代替最终文本；长度截断、refusal、工具调用、无文本、模型标识不匹配都不会被当成有效裁决。

本项目没有对所有服务商、所有推理服务器逐一联调。新模型先验收三题，再做全量；模型只是能返回一句话，不足以证明长历史、结构化材料及审核都兼容。

## 需要填写的 profile

```json
{
  "provider": "openai",
  "model": "YOUR_SERVED_MODEL_ID",
  "expected_model": "YOUR_RETURNED_MODEL_ID",
  "base_url": "http://127.0.0.1:8000/v1",
  "credential_env": "LOCAL_MODEL_API_KEY",
  "temperature": 0.0,
  "max_tokens": 16384,
  "max_tokens_field": "max_tokens",
  "reasoning_effort": null,
  "seed": null,
  "supports_seed": false,
  "context_limit": 128000,
  "timeout": 300
}
```

这些容量只是模板值，必须按所选模型和实际服务设置调整，不能当成模型能力声明：

- `model` 是请求里的服务模型名，可以是本地 served-model alias；不是必须为权重文件夹路径。
- `expected_model` 是服务响应的精确模型标识。非空时严格匹配；即使为空也仍要求响应带非空 model。模型名/别名不等于提供方内部权重版本可固定，应另外记录权重 revision、文件哈希、量化、服务版本及 chat template。
- `temperature: null` 会省略该参数。只有支持时才传数值；0 也不保证严格确定性。
- `max_tokens_field` 支持 `max_tokens` 或 OpenAI 兼容服务的 `max_completion_tokens`。Anthropic 使用 `max_tokens`。输出需容纳该服务的最终回复及相关计费方式，过低会产生截断。
- `reasoning_effort` 仅 OpenAI 兼容接口支持，允许 low/medium/high/max 或 null。具体模型不接受时设 null；不要继承 MiniMax 的 max。
- `seed` 只有 `supports_seed: true` 才能设置；按任务生成请求 seed。不要把选择发送者的 `selection_seed: 42` 当成服务端随机种子。
- `context_limit` 为服务实际可用上下文容量护栏，包含累计历史；不是扩大模型上下文的开关，也不是按服务器精确 tokenizer 计量的承诺。
- `timeout` 为每次 HTTP 请求秒数。慢速本地推理可提高超时、降低题目并发；不要通过截掉累计历史来提速，否则改变实验。

`debate_models`、`material_model`、`audit_model` 引用 profiles 中的名字。当前同模型实验让三者引用同一个 profile；更换模型时一并更新。若固定审核模型而只换被评模型，应明确记录这种研究设计，不能宣称与当前同模型设置完全相同。

## 本地开源模型

先在框架之外启动推理服务器，确认它支持 system/user/assistant 的累计历史、非流式 Chat Completions、所需上下文与 JSON 指令。框架不负责权重下载、GPU 安装或服务器部署。

复制 `configs/averitec-rq1-v10-local-3-v2-template.json` 为 `configs/my-local-3.json`。填写 profile，将 `network_enabled` 改为 true。本地 HTTP 也属于显式启用的网络后端。

只允许 `http://localhost`、IPv4 回环地址和 IPv6 回环地址，其他服务使用 HTTPS。若模型在远程服务器，可使用 HTTPS，或先建立 SSH 本地端口转发，再将框架指向本机端口；不向公网明文传送真实密钥。

若服务器不要求鉴权，仍设置这个专用非秘密占位变量，因为当前 HTTP 后端统一要求有值，并发送 Bearer 头：

```powershell
$env:LOCAL_MODEL_API_KEY = 'local-no-auth-token'
python -X utf8 -m controlled_mad validate --config configs/my-local-3.json
python -X utf8 -m controlled_mad plan --config configs/my-local-3.json
python -X utf8 -u -m controlled_mad main-run `
  --config configs/my-local-3.json --run-id my-local-pilot-v1 `
  --runs-root ../runs --question-batch-size 3 --concurrency 3 --item-pipeline
```

有鉴权时使用服务器实际要求的密钥。占位值不适用于要求真实鉴权的服务。显存或排队不足时可改成 concurrency=1，它只改变调度，不改变消息内容或实验条件。

## 闭源服务商

复制 `configs/averitec-rq1-v10-hosted-3-v2-template.json` 为 `configs/my-hosted-3.json`。填实际 HTTPS 地址、模型名、返回名、支持的参数，并开启 `network_enabled`。在本机配置 `HOSTED_MODEL_API_KEY` 环境变量，然后运行：

```powershell
python -X utf8 -m controlled_mad validate --config configs/my-hosted-3.json
python -X utf8 -m controlled_mad plan --config configs/my-hosted-3.json
python -X utf8 -u -m controlled_mad main-run `
  --config configs/my-hosted-3.json --run-id my-hosted-pilot-v1 `
  --runs-root ../runs --question-batch-size 3 --concurrency 3 --item-pipeline
```

也可显式加 `--credentials-file <本机凭据文件>`。文件包含配置指定的凭据变量或 MODEL_API_KEY / OPENAI_API_KEY 中唯一一致的值；可选 OPENAI_BASE_URL、MODEL_NAME 必须与冻结配置匹配。含多个不同密钥会拒绝猜测。该方式仅用于本次所有角色共享同一服务/模型/凭据的启动；多模型使用各自环境变量。

Anthropic 兼容服务将 provider 设为 anthropic、reasoning_effort 设为 null、max_tokens_field 设为 max_tokens，基础地址通常止于 `/v1`。框架发送 x-api-key 和 anthropic-version 头。其他返回格式需要新增后端适配，不能靠修改 provider 名绕过校验。

## 三题与人工材料核查通过后扩展

检查 48/48 格、21/21 份合格材料、T1 共享和 T2/T3 路由。三题是接口与流程验收，不要求出现标签变化。正式全量前按[人工验证指南](human-validation.md)检查试跑材料；自动合格不等于人工证明语义保持。

复制 `averitec-rq1-v10-equal-rounds-500-v2-template.json` 为新模型的 500 题配置；复制三题已验证的 profiles、debate_models、material_model、audit_model，开启网络，保留 full500 的预算和 inference 设置，再 plan 和启动新 run-id。每模型重新生成 S0、改写与讨论。保留权重/服务版本记录，服务商 token plan 等待刷新后，用原配置和原 run-id 续跑。

默认将三题作为独立先导，全量从新的500题run开始。v2先导与全量的人工抽样计划不同，现有复用接口会校验并拒绝不兼容配置；不要直接加旧的reuse参数，或把未审核的继承称为“剩余497题”。

不要在某个 run 跑到一半后更改模型、服务地址、解码参数、容量字段、协议、源文件位置或提示；这些都可能改变冻结配置及任务身份。确需改变时新建 run。仅凭据值轮换或并发调整可以沿同一运行恢复，前提是模型与实验内容保持原样。
