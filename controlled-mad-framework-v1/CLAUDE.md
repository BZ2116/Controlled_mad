# 受控 MAD 框架工作约定
工程约定以本文件和 README.md 为准；本地研究决策记录放在 docs/（该目录不发布）。真实模型调用须另冻结配置与预算。
源码在 src/controlled_mad；测试在 tests；演示在 examples/configs；提示在 prompts；文档在 docs；运行产物只在 runs/<run_id>，不可复用 run_id。
公开输入数据放在 datasets/<dataset_id>/，只收录有来源与许可说明的原始文件；datasets/README.md 记录来源、版本、校验和与使用状态。与数据配套的可发布配置放 configs/，数据 source 写相对路径。个人绝对路径、原始运行日志、缓存、密钥和临时派生文件不进入 datasets/；更新源数据时使用新文件名并记录哈希，不静默覆盖。
三轮主实验缺省保持原协议；五轮 RQ2 补充实验必须显式写 experiment、subset 并通过独立 rq2 命令启动。两个实验使用不同 run_id，不能把旧三轮轨迹接长。Windows 使用 PowerShell 命令和短 runs-root；所有文本输入指定 UTF-8。
临时文件仅用研究根目录 .tmp。旧实验只读。raw、事件、快照只追加；密钥仅从明确配置的环境变量读取，不写文件。
先读当前文件再合并编辑；使用 unittest 验证关键行为，完成前运行全套和 mock 演示。无 Git 仓库时用内容/校验和检查新产物，不初始化仓库。
并发只按题、不按任务：一个 worker 独占一道题，题内串行。依据是 task_id 为含 item_id 的内容寻址哈希，故并发与串行的内容寻址对象集合必须完全一致，改动后用 test_concurrency 的等价性用例守住。执行策略（--concurrency、--retry-http）不进冻结配置，只写 execution_policy 事件。store 的跨进程文件锁只归主线程 execute_ready，worker 只拿进程内 RLock；HTTP 调用不得持锁。
内容寻址集合必须是实验内容的函数，不得是调度时序的函数：builder 每轮重建都会重算派生对象，半成品一律不落盘（comparison 只在 O、R 都跑满时写入）。中断续跑、并发度、refresh 粒度都不得改变任何集合，改动后用 test_concurrency 的续跑等价性用例守住。
