# 运行、查看与断点续跑

当前推荐v2配置，完整规范见[study-protocol-v2](study-protocol-v2.md)。先对新服务试跑材料做人工核查；完成16格本身不等于操纵验证通过。导出、导入和论文级汇总见[人工验证说明](human-validation.md)。

所有命令从框架目录执行。可先 `python -m pip install -e .`，或直接设置 `$env:PYTHONPATH = 'src'`。Linux/macOS 用 `export PYTHONPATH=src`，命令内容相同，续行符改为 shell 对应写法。

## 完整实验前

1. 新模型先填写三题模板并完成真实验收。MiniMax-M3.1-Flash 已有三题配置和一次真实验收；其他服务的兼容性不由此推出。
2. 确定模型、服务、解码、上下文、预算和数据版本，完成 `validate`、`plan`。两者不调用模型；检查 500 题、8,000 个终局、预算余量。
3. 使用新的 run-id。完整实验建议每批 10 题、按题并发 3；题内调用串行。实验配置不随暂停与续跑变化。

```powershell
python -X utf8 -m controlled_mad validate --config configs/averitec-rq1-minimax-equal-rounds-500-v2.json
python -X utf8 -m controlled_mad plan --config configs/averitec-rq1-minimax-equal-rounds-500-v2.json
```

MiniMax 凭据环境变量为 MINIMAX_API_KEY；默认不自动读文件。需要文件时在 main-run 命令显式加 `--credentials-file <本机路径>`。密钥文件不上传 GitHub。

## 启动与恢复使用同一条命令

```powershell
python -X utf8 -u -m controlled_mad main-run `
  --config configs/averitec-rq1-minimax-equal-rounds-500-v2.json `
  --run-id minimax-continued-peer-500-v2 `
  --runs-root ../runs `
  --question-batch-size 10 `
  --concurrency 3 `
  --item-pipeline
```

每题材料冻结后完成本题讨论，不先跑全量 500 题某一阶段。每批十题的 16 格全部完成后才进入下批。`--single-pass` 与等轮次协议冲突，会被拒绝。早期命令中的 `--reuse-matrix-source` 不用于把旧 I-T1/M-T3 全量结果移植到新协议。

Ctrl+C 可中止。程序退出后，再执行同一命令；保存的有效结果继续使用，未完成请求继续推进。不要为恢复改 run-id、删除历史文件或重建新目录。相同 run 不要同时开两个写入窗口，跨进程锁会阻止重复写入。

断点缓存 `.resume-checkpoint.json.gz` 是可重建的索引。常规启动使用断点并读取必要对象，不逐文件做完整审计；索引丢失时需要从原事件恢复，可能较慢。实际读取对象仍校验提交标记和哈希。不要只复制断点文件：恢复需要整个 run 目录及原配置引用的源文件。

## 配额、有限重试与失败

main-run 自动启用有限的 HTTP/传输退避重试，支持 Retry-After。连续未决达到停止条件后保留断点退出；不会无限等待五小时。额度刷新后再执行原命令即可。任务级失败、无效输出、材料不合格与传输未知分别记录。

低层 `run` / `resume` 命令的 HTTP 重试默认关闭，需要显式 `--retry-http`；不要把低层默认值套用到 main-run。

默认完整批次模式遇到缺格停下检查。`--continue-on-error` 是可选的“有限修复后记录跳过”策略，不能把跳过题当作16格完成；严格完整实验不要仅以进程退出成功判断完成。服务长期失败、上下文不足、超预算或材料一直不合格时，先查缺失原因。追加重试/重解析保留旧原始响应，不直接改 JSON 文件或伪造裁决。

## 进度与验收

```powershell
python -X utf8 -m controlled_mad status --run-id minimax-continued-peer-500-v2 --runs-root ../runs
python -X utf8 -m controlled_mad matrix-status --run-id minimax-continued-peer-500-v2 --runs-root ../runs
python -X utf8 -m controlled_mad failure-status --run-id minimax-continued-peer-500-v2 --runs-root ../runs
```

matrix-status 是只读逐格检查，完整输出较长。最终要求：500/500 题完整；8,000/8,000 格均 complete；每题八种材料齐全，I-T3/M-T3 各有真实三票。no_majority 是有效无多数，不等于运行失败。

整体任务百分比会随材料资格和任务图展开变化，不应拿它当完成题数。以题目完成数、批次和终局格数为准；一个正在运行的题可以已有多个过程快照但尚未有16个终局。

完整审计可单独执行，耗时比普通续跑大：

```powershell
python -X utf8 -m controlled_mad verify --run-id minimax-continued-peer-500-v2 --runs-root ../runs
```

## 报告与导出

main-run 完成时自动生成 reports 与 exports。只重算离线分析：

```powershell
python -X utf8 -m controlled_mad analyze --run-id minimax-continued-peer-500-v2 --runs-root ../runs
python -X utf8 -m controlled_mad trace --run-id minimax-continued-peer-500-v2 --runs-root ../runs --item-id averitec_0000
```

报告按内容标识区分版本；旧版本留存。核对结果时使用同一 analysis_id 的 result_matrix、condition_summaries、item_effects 等导出，不混用早期未齐版本。新全量配置的 inference 在离线分析阶段计算，不请求模型。不要为给旧 run 增加统计而手改其 manifest；独立离线再分析应另记设置与来源。

某些通用报告模板含“模拟后端输出只能用于工程验收”的条件性提醒；该句本身不等于当前 run 使用 mock。实际来源以冻结 profile、started 事件、原始响应和模型返回标识为准。

## 换电脑或迁移目录

源数据路径在冻结配置中解析为绝对路径，参与配置一致性检查。当前实现不承诺把整套运行随意移动后立即按原配置续跑。优先保持原路径、完整目录与代码版本；迁移前核对 manifest 和源文件哈希，不能靠改 manifest 抹平差异。备份保留整个 run、对应配置、数据、提示及版本记录。

旧实验和更换后的模型分别保存，不能按题拼接为一次同模型全量实验。
