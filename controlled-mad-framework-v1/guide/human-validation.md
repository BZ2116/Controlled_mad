# 人工材料验证与回复编码

两类工作独立：材料验证检查O/R内容保留与风格操纵；回复编码检查后续回复出现了什么。软件只提供预选、可见文本、引用校验和一致性汇总，未收到实际标注时保持缺失。

## 先导出材料包

v2全量配置在模型调用前预选50题（seed=20261022），三题配置预选三题。所有七操作都有自己的材料对，最多350/21对。已生成但模型审核失败的最终候选若有文本也可进入包，不只挑通过或有明显效应的例子。

```powershell
$env:PYTHONPATH = 'src'
python -X utf8 -m controlled_mad material-validation-export --run-id my-v2-run --runs-root ../runs
python -X utf8 -m controlled_mad material-validation-status --run-id my-v2-run --runs-root ../runs
```

导出返回JSONL路径及冻结代码本。包里有 validation_id、题号、原文、改写、长度；不提供操作、模型、金标和实验结果。不能声称文本完全盲法。未生成的材料不伪造文本，状态报告同时列出计划与可用数量。

每名标注者每对材料提交一个JSON对象，JSONL每行一条，字段必须完整：

- validation_id：照抄导出包。
- coder_id：`coder-a` 或 `coder-b`。两人独立完成，不能先看对方判断。
- codes：facts_preserved、reasoning_preserved、scope_preserved、uncertainty_preserved，分别填yes/no/uncertain。判断命题、推理、范围和确定程度是否保持，不把标签相同当作内容相同。
- style_scores：original、rewritten各含ethos/logos/pathos三个1–5整数，按代码本对实际表达打分。不要按“改写理应更强”填分。
- rationale：上述四字段与ethos/logos/pathos共七个键，每键包含source、quote、note。source为original或rewritten；quote必须逐字存在于指定文本，note说明理由。评估缺少某特征时仍引用相关文本并解释，不制造不存在的引文。

单字段格式示意（不是可直接导入的完整标注，也不是实际人审结果）：

```json
{"facts_preserved":{"source":"original","quote":"这里必须替换为原文中的逐字引文","note":"这里填写实际比较依据"}}
```

导入和持久保存一致性报告：

```powershell
python -X utf8 -m controlled_mad material-validation-import --run-id my-v2-run --runs-root ../runs --source ../annotations/material-coder-a.jsonl
python -X utf8 -m controlled_mad material-validation-import --run-id my-v2-run --runs-root ../runs --source ../annotations/material-coder-b.jsonl
python -X utf8 -m controlled_mad material-validation-status --run-id my-v2-run --runs-root ../runs
```

报告保存到exports的内容寻址JSON：覆盖、逐语义字段一致率/kappa、未解决分歧、每操作双人均确认保留的计数、两文本平均风格分数变化。不自动合并分歧，不自动剔除结果，也不把双人一致当成语义等价证明。

## 再做回复编码

```powershell
python -X utf8 -m controlled_mad coding-export --run-id my-v2-run --runs-root ../runs
python -X utf8 -m controlled_mad coding-status --run-id my-v2-run --runs-root ../runs
```

主配置预选50题×每题最多12个逻辑回复单位=最多600条。所选单位ID在模型输出前冻结，分层覆盖共享T1、I-T2、M-T2、I-T3、M-T3；无效或缺失不会换成另一个单位。每题各表达版本和代理的具体覆盖由固定seed选择，完整实际分布可从消息审计与选定ID核对。该抽样是探索性过程编码，不是每条传播链的完整人工审计。

包中有coding_id、回复、原文引用、实际展示的目标评论和可见消息；去掉显式操作、分支和金标，文本本身仍可能泄露处理。每条记录：

- coding_id、coder_id。
- codes：stance_retained、evidence_reassessed、expression_reproduced、new_arguments、new_facts五键，各yes/no/uncertain。
- rationale：同样五键，各含source/quote/note。source为reply、original或displayed，quote必须逐字来自相应文本。

不要把“标签相同”推断成表达复现，不要把新论据等同于新事实。新事实相对于完整输入及可见证据判断，不能只与目标短评论比较；可见消息完整保留供核对。

```powershell
python -X utf8 -m controlled_mad coding-import --run-id my-v2-run --runs-root ../runs --source ../annotations/reply-coder-a.jsonl
python -X utf8 -m controlled_mad coding-import --run-id my-v2-run --runs-root ../runs --source ../annotations/reply-coder-b.jsonl
python -X utf8 -m controlled_mad coding-status --run-id my-v2-run --runs-root ../runs
python -X utf8 -m controlled_mad analyze --run-id my-v2-run --runs-root ../runs
```

v2 coding-status将一致性报告写入exports。重新分析生成含当前人工覆盖的新报告，旧报告留存。报告内明确区分尚无标注、单人标注和双人完成。原始标注不得覆盖；冲突不是软件自动裁决的对象，应另保存人类裁决记录，不能静默改旧批次。

## 论文级统计汇总

每个模型/数据集正式run指定一份最终冻结的analysis文件；不得混用试跑和全量，不得只挑显著run。示例：

```powershell
python -X utf8 tools/combine_study_inference.py `
  --analysis ../runs/model-a-data-a/reports/analysis-REAL_ANALYSIS_ID.json `
  --analysis ../runs/model-b-data-a/reports/analysis-REAL_ANALYSIS_ID.json `
  --output ../paper-primary-inference.json
```

替换真实文件名，保留同目录的.committed提交标记。工具核对哈希，拒绝重复的数据/模型运行设置/操作，不跨数据集合并题目；重新校正全部提供的预定T3 verdict检验。不可估计项仍占家族位置但p保持NA，输出文件必须是新文件。模型fingerprint表示冻结服务配置，不能替代权重来源核查。

旧run没有预选计划时不能假装事先做过预选；可以另做明确标为事后的独立研究，但不修改旧manifest。上述命令不调用模型，不使用关键词生成“人工标签”。
