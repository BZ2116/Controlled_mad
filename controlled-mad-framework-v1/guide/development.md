# 开发与验证

Python 3.11+；运行时标准库。项目用 setuptools 打包，安装需要构建工具，不依赖 OpenAI/Anthropic Python SDK。

## 目录

| 目录 | 职责 |
| --- | --- |
| src/controlled_mad | 配置、调度、消息路由、执行、断点、解析、统计与导出 |
| src/controlled_mad/resources | 随安装包发布的默认提示词，创建 run 时冻结 |
| prompts | 提示词可读副本；需要覆盖时在 config.prompts 显式指定 |
| configs | 当前推荐配置、模板、演示和历史配置，见其 README |
| examples | 合成数据与模拟回复 |
| datasets | 公开原始研究输入及来源许可；运行生成物不在此目录 |
| guide | 可发布使用文档 |
| tests | unittest，含并发/中断等价性与 mock 流程 |
| tools | 离线验收工具；旧 single-pass 解析脚本仅用于旧协议 |

本地 docs 是个人研究记录，runs 是不可覆盖的实验产物，均不上传。模型权重、密钥文件、缓存和构建产物受 .gitignore 排除。不要移动旧运行以“整理目录”。

## 验证

PowerShell：

```powershell
$env:PYTHONPATH = 'src'
python -X utf8 -m unittest discover -s tests -v
python -X utf8 -m compileall -q src tests tools
```

测试使用模拟模型及受控本机 HTTP 测试服务，不调用真实提供方。完整套件包含 fsync 与子进程恢复测试，Windows 上可能较慢。不要只凭进程启动判断验收成功，应检查 unittest 的总数和最终 OK。

mock 手动验收命令见主 README。模拟数据不能当成研究结果。新服务的真实三题验收需由研究者设置凭据与调用预算后另行启动。

## 修改约束

- 冻结配置显式区分协议。已有完整矩阵无 matrix_protocol 时保持 single-pass 行为，新实验显式 equal_rounds_v10，不偷偷改变旧 hash 或轮次数。
- 内容寻址对象由实验内容决定，不由并发/时间/中断次数决定。只按题并发，一个 worker 独占一道题，题内串行。
- T1 在 I/M 间共享；下一轮只用上一轮完整快照，不产生同轮泄漏。只在 O/R 完整时落盘 comparison。
- raw、parse、snapshot 与事件不可覆盖；错误修复追加新尝试或解析。断点只作索引，不取代原始证据。
- 模型与任务适配通过 profiles 和后端契约，不因更换服务改动实验提示、截断历史或改变主估计量。
- 本机 loopback HTTP 与外部 HTTPS 分开；URL 不嵌入凭据，密钥只从明确环境变量或显式指定文件载入进程。

构建 wheel：`python -m pip wheel --no-deps .`。wheel 包含执行器和资源提示；数据、配置和演示属于仓库配套文件，运行正式实验时保留仓库副本。
