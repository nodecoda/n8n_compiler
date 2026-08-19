# 安全策略

## 报告漏洞

n8n_compiler 是开发期编译器工具，不接受外部漏洞赏金。发现安全问题时：

- **公开仓库禁止**在 issue/PR/commit 中描述攻击向量或漏洞类型（攻击者会监控
  开源仓库信号）。请用中性语言描述功能修复（如 "add payload size validation"）。
- 涉及部署凭据（`N8N_API_KEY` 等）的问题，优先联系仓库所有者（private 渠道）。

## 密钥纪律

- 命令行禁止硬编码密钥；`scripts/execute_deploy.py` 要求 `N8N_API_KEY` 环境变量
  （ps 不可见），CLI `--api-key` 仅应急。
- 本仓库任何提交均不得包含真实凭据（提交前跑敏感扫描）。
