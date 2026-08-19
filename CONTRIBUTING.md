# 贡献指南（Contributing）

感谢参与 n8n_compiler。本仓库遵循与 coze_compiler 相同的工程纪律：**行为由回归
测试锁定，改动先红后绿，失败显式化不静默降级**。

## 环境

- Python ≥ 3.10（纯标准库，零第三方运行时依赖）
- Node.js ≥ 16 + `npm install`（acorn 桥，Code 节点 JS 严格解析；`scripts/js_parse.mjs`）
- 可选：完整 n8n 仓库用于批量矩阵（默认 `/home/dev/n8n`，`N8N_REPO` 环境变量覆盖；
  缺失时矩阵测试自动 skip，不伪造结果）

## 常用命令

```bash
python3 -m unittest discover -s tests          # 全量（unit + integration + matrix）
python3 -m unittest tests.test_parser          # 单模块
python3 tests/coverage.py --threshold 90       # 覆盖率门禁（CI 同款）
python3 cli.py check workflow.json             # 解析 + 静态校验
python3 cli.py compile workflow.json -o out.ir.json
```

可安装模式（开发推荐）：`pip install -e .` 后使用 `n8n-compiler` 命令。

## 纪律

1. **先红后绿**：任何修复/特性先写失败回归测试，再实现，最后全绿收尾。
2. **矩阵不回退**：`tests/test_batch_matrix.py` 固化 n8n 官方仓库 143 工作流分类
   （130 PASS / 10 CYCLIC / OTHER）；改动不得静默降低 PASS 集，分类变化须显式
   更新断言并说明理由。
3. **lint 零噪音**：提交前 `ruff check .` 必须通过（规则集见 `pyproject.toml`）。
4. **只改编译器**：对照基准 `/home/dev/n8n` 只读，禁止修改。
5. **提交信息**：Conventional Commits（`feat:` / `fix:` / `refactor:` / `docs:` /
   `test:` / `chore:` + 中文描述），一次提交一个关注点。

## CI 与 n8n 快照策略

CI 的 matrix 层通过 sparse-checkout 拉取 **n8n master 滚动快照** 的测试夹具，
而 `test_batch_matrix.py` 的 143 工作流断言固化于 2026-08-18 快照。若 n8n
master 删除/重命名夹具文件导致 PASS/CYCLIC 集合失配，CI 会红——这是**预期信号**：

1. 先确认失配是 n8n 上游变更而非本编译器回归（对比失败集合与新增/删除文件）。
2. 确认后**显式更新断言**并注明所依据的 n8n commit 或日期（沿用"矩阵不回退"纪律）。
3. 需要完全可复现时，把 sparse-checkout 固定到具体 commit/tag（在 CI 中加
   `ref:` 参数），默认跟随 master 以持续暴露上游变化。

## 测试分层

| 层 | 依赖 | 说明 |
|---|---|---|
| unit | 无 | parser / checker / type_system / scope / round-trip |
| integration(node) | Node.js + acorn | `tests/test_code_js.py` |
| matrix(n8n repo) | n8n 仓库 | `tests/test_batch_matrix.py` |

缺依赖按层 skip（`skip_unless_n8n_repo` 等守卫），不伪造结果。

## 架构文档

审核/设计文档在 `ana-docs/`（每轮架构审核 + 修复记录）。重大改动前建议先出
审核/设计文档再实现。
