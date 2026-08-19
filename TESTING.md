# 测试治理（TESTING.md）

n8n Compiler 的测试策略、命令与覆盖率约定。测试全部使用 Python 标准库
（`unittest` + `trace`），零第三方依赖，`python3` 即可运行。

## 分层与依赖

| 层 | 依赖 | 说明 |
|---|---|---|
| unit | 无 | 纯 Python：parser / checker / type_system / scope / 序列化 round-trip |
| integration(node) | Node.js + acorn | `tests/test_code_js.py`：Code 节点 JS 严格解析（acorn 桥） |
| matrix(n8n repo) | n8n 仓库 | `tests/test_batch_matrix.py`：对 n8n 自带 143 个真实工作流跑全链路 |

- 仓库路径解析：`N8N_REPO` 环境变量优先，默认 `/home/dev/n8n`（见 `tests/helpers.py::n8n_repo`）。
- 缺依赖时按层 skip，不伪造结果：`require_n8n_repo` / `skip_unless_n8n_repo` 守卫。

## 运行

```bash
# 全量（unit + integration + matrix，依赖齐全时）
python3 -m unittest discover -s tests

# 单文件（开发时主循环）
python3 -m unittest tests.test_parser
python3 -m unittest tests.test_expression -v

# 覆盖率（全量测试 + 模块级语句覆盖）
python3 tests/coverage.py                 # 完整报表
python3 tests/coverage.py --quiet         # 只输出汇总
python3 tests/coverage.py --threshold 90  # CI 门禁：低于 90% exit 1
```

## 覆盖率说明（重要）

`tests/coverage.py` 用标准库 `trace` 做解释级计数，`ast` 统计可执行行。
两条已知特性，解读报表时必须知道：

1. **trace 只记录「至少执行一次」的行**——若直接对 trace 结果求百分比会假报
   100%。必须用 ast 可执行行作分母（工具已实现）。
2. **多行 import / 装饰器 / 同行多语句会偏离真实值**：`from x import (a, b, c)`
   的续行会被 ast 计为可执行行但永不触发 trace 事件，导致模块显示
   `>100%`（如 `values/reference.py`）或 import 密集模块偏低（如
   `ast_nodes/mappings.py`）。**这些不是真实缺口**——判断缺口用
   `checker/validator.py` 这类函数体模块，并配合代码审查确认。

治理以**相对基线**为准：记录每次治理后的总数，禁止回退。当前基线：**92.8%（2721/2932，281 tests，2026-08-19，P1-2 AI 链 Code 静态分析后；runtime/ + 顶层模块已纳入口径）**。注意：2026-08-19 口径修复——`_is_project_module` 的 `rel.name` vs `TOP_MODULES` 不匹配，typed_ir/manifest/cli 曾长期未进报表（数字虚高）；修复后为真实值，CI 门禁建议 `--threshold 85`（容忍 trace 假象），
趋势看总数。

## 文件结构

```
tests/
  helpers.py            fixture 工厂（webhook/set/code/if/switch/limit/chain）
                        + n8n_repo() + skip 守卫（所有测试共用，勿重复造）
  __init__.py           sys.path 统一引导（测试内禁止 sys.path hack）
  coverage.py           零依赖覆盖率工具（trace + ast）
  test_parser.py        workflow JSON -> AST（节点/连接/表达式/adaptors）
  test_expression.py    表达式解析分类（$node/$env/$json/复杂表达式 -> ParsedRef）
  test_code_js.py       JS 子系统（acorn 严格解析 + 静态契约 + n8n 坏代码样例）
  test_checker.py       三类误报回归 + RAG fixture 端到端 + 真实错误仍检出
  test_checker_coverage.py  validator 缺失分支（手工构造 AST：unknown_source/
                        target、type_mismatch、retry_policy、WorkflowValidationError）
  test_type_roundtrip.py  DataType/TypeInfo/Connection/mappings.load_typed_node
  test_misc_coverage.py  序列化 round-trip、scope/symbol_table、JS 桥失败路径
  test_alignment_bounds.py 对拍边界回归（$node/$input 访问器纪律、模板提取、
                        to_index 的 IR 兼容/校验边界）
  test_compiler.py      compiler 全链路（check/compile/export/digest）
  test_decompile.py     IR -> n8n JSON 反编译 round-trip（编译无损性
                        + 篡改/损坏 IR 显式拒绝守卫）
  test_deploy.py        runtime.deploy 部署 adapter（urlopen mock：
                        请求形状 / 显式失败 / digest 拒绝）
  test_cli.py           cli.py 入口（redirect_stdout 消除 issue JSON 日志污染）
  test_batch_matrix.py  n8n 仓库 143 工作流矩阵回归（130 PASS / 10 环 / 3 其他；
                        AI 链工作流随 IR v2（P1-1c）撤回 PASS）
  fixtures/             预留 fixture 目录
```

## 真实实例验证（需远端 n8n，非单元测试）

两条等价路径均已在 nodecoda-production（n8nio/n8n:latest）跑通 7/7：

- `scripts/execute_matrix.py`：**CLI import 路径**——本地 build 产物 ->
  远端 `n8n import:workflow` + `n8n execute --id` -> 拉回断言（`assert` 子命令）。
- `scripts/execute_deploy.py`：**REST 部署路径**——`deploy`（本地起 SSH 隧道 ->
  `deploy_to_n8n` POST /api/v1/workflows）-> `execute`（远端 docker exec +
  独立 `N8N_RUNNERS_BROKER_PORT`）-> `execute_matrix.py assert` 复用矩阵断言。

真实实例抓出的契约差异（mock 测不到）已修复并固化：settings 必填、id readOnly、
task broker 端口冲突。详见 `ana-docs/decompile-roundtrip.md`「REST 部署真实实例验证」。

## 治理规则

1. **新增 fixture 一律进 `tests/helpers.py`**，禁止在测试文件内散落重复构造；
   禁止跨测试模块 import 私有函数（从 `from tests.test_parser import _x` 迁到
   helpers）。
2. **禁止 `sys.path.insert` hack**：统一由 `tests/__init__.py` 引导。
3. **测试命名**：`test_<行为>`，用 `assertEqual(实际, 期望)` 而非裸断言；
   失败信息要能直接定位是哪个分支（如断言 issue code 列表）。
4. **修复 bug 先加回归测试**：n8n 仓库自带的坏样例（`1aaa;` 语法错误）已固化
   在 `test_code_js.py`；表达式分类回归在 `test_expression.py`。
5. **外部 gate 失败必须显式**：Node/acorn 不可用抛 `JSInfraError`（基础设施
   错误），测试断言其不静默降级；Python 模式 Code 节点 v1 明确报错不支持。
6. **提交前**：跑全量 + `tests/coverage.py`，确认无回退；README 的矩阵数字
   与实际测试结果保持一致。
