# 受控 MAD 框架工作约定
工程约定以本文件和 README.md 为准；本地研究决策记录放在 docs/（该目录不发布）。真实模型调用须另冻结配置与预算。
源码在 src/controlled_mad；测试在 tests；演示在 examples/configs；提示在 prompts；文档在 docs；运行产物只在 runs/<run_id>，不可复用 run_id。
临时文件仅用研究根目录 .tmp。旧实验只读。raw、事件、快照只追加；密钥仅从明确配置的环境变量读取，不写文件。
先读当前文件再合并编辑；使用 unittest 验证关键行为，完成前运行全套和 mock 演示。无 Git 仓库时用内容/校验和检查新产物，不初始化仓库。
