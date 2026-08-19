# n8n Compiler

n8n Workflow 的 AOT 编译器前端（Python，纯标准库）：把 n8n Workflow JSON
（节点 + 连线 + 表达式）解析为强类型 AST，静态检查，编译为可序列化的强类型
typed IR v2（v1 兼容装载），供独立轻量运行时或部署方 runtime adapter 消费。

架构参照 `/home/dev/coze_compiler`（Coze 编译器）的分层：

```
workflow JSON ─→ parser ─→ 强类型 AST ─→ checker ─→ compiler ─→ typed IR v2
                     (nodes/connections/expressions)      (拓扑序+digest+manifest)
```

## 使用

```bash
python3 cli.py check workflow.json                  # 解析 + 静态校验
python3 cli.py compile workflow.json -o out.ir.json # 编译 typed IR
# 注：README 历史版本提到的 `export` 命令尚未实现（cli.py 现仅 check/compile）
```

## 状态

- v0.1：编译前端（check / compile）。运行时（`runtime/`）首个组件已落地：
  `runtime/decompile.py`（typed IR -> n8n Workflow JSON，round-trip 验证编译无损）。
- **A2 真实执行验证已完成（2026-08-18）**：反编译产物在真实 n8n
  （`n8nio/n8n:latest`，nodecoda-production 腾讯云内网 docker 源）导入 +
  执行成功（`result: 42`，status=success）。可复跑：
  `python3 scripts/execute_verify.py build` 生成产物并打印远端验证命令。
  验证抓出并修复 workflow id 未还原缺口（n8n 导入要求 id 非空唯一）。

## 反编译（round-trip）

`runtime/` 首个组件：把编译器产出的 typed IR v2 还原为 n8n 可导入的工作流 JSON，
与 parser 构成闭环 `n8n JSON -> parse -> compile -> IR -> decompile -> n8n JSON`，
验证编译无损，也为部署 adapter（把 IR 部署回 n8n）打基础。

```python
from runtime.decompile import decompile_to_workflow, decompile_ir_json

# IR dict（compile_ast(...).to_dict() 产物）-> n8n Workflow JSON
wf = decompile_to_workflow(ir, name="my-workflow")

# 或直接吃 IR JSON 文本（入口过 validate_typed_ir，篡改/损坏显式失败）
wf = decompile_ir_json(open("out.ir.json").read(), name="my-workflow")
```

行为约定：

- 剔除合成节点 `synthetic.exit` 的 `__exit__` 及收口边（`synthetic.entry`
  当前不产生，防御性一并剔除）；IR `from_port`（main/main_N）-> n8n 输出
  端口索引，`to_index` -> n8n 边 `index`。
- IR 携带 settings（v2 起，源 settings 原值；缺失回退编辑器默认 v1——REST
  schema 强制字段存在）；不携带 name/pinData -> 由调用方传 `name`（默认
  `"decompiled"`），未入 IR 的字段不还原：保证编译语义往返，不保证完整
  审计往返。
- 数值整值浮点归一为 int、`parameters` 恒写入（含空 `{}`），对齐 n8n 原生
  导出形状。
- 反编译入口必须过 `validate_typed_ir`（白名单 + digest），篡改/损坏的 IR
  显式失败，不静默产出半成品（与外部 gate 同纪律）。

回归固化在 `tests/test_decompile.py`（round-trip 等价 + 守卫）。

## 编译边界（round-trip 丢失清单）

编译器保证**编译语义往返**（节点/连接/参数/表达式/错误策略/凭据引用/settings），
以下字段**明确不携带**（锁定现状，防未来静默改变；回归见
`test_node_aux_fields_dropped_documented`）：

| 字段 | 出现率（矩阵 143 文件） | 说明 |
|---|---|---|
| `notes` | 103 | 画布批注，纯展示，无执行语义 |
| `webhookId` | 93 | 服务端生成（REST create 自动补）；CLI import 不补——双路径不对称已知（P3-1），IR 不携带避免把陈旧 id 写回 |
| `disabled` | 3 | 节点停用状态；v2 不还原（源 disabled 节点 round-trip 后变 enabled）——已知边界 |
| `executeOnce` / `alwaysOutputData` | 各 1 | 节点级执行修饰；不还原 |
| `pinData` | — | 固定输入数据（编辑器调试用）；不入 IR |

## 注册表覆盖（泛型透传）

节点注册表（`ast_nodes/node_type.py`）声明端口形状/类型，未注册类型落
`GenericNode`（1 入 1 出 ANY）**透传**，检查/编译仍全通。矩阵实测：
**75 种节点类型，27 注册 / 48 未注册（64% 落 GENERIC）**。已知风险：

- 未注册多输出终端节点的 exit 收口只收 main 端口——与 n8n「未连端口即
  丢弃」行为等价，无害（P3-7）；
- 未注册 trigger（mcpTrigger 等）由「类型名含 trigger」启发式兜底识别入口
  （P2-1 双保险）；
- `@n8n/n8n-nodes-langchain.code`（AI 链内联 Code，supplyData 工厂）已注册为
  CODE kind，与普通 Code 节点同走 acorn 静态通道（P1-2）：语法检查 + 契约
  （工厂模式：return 组件实例，无 items 输入）+ ESTree 进 IR。AI 链 JS 盲区关闭；
- wait/noOp 等透传节点无副作用，GENERIC 透传无损。

## 部署（runtime/ 第二个组件）

`runtime/deploy.py` 把反编译产物通过 n8n REST API（`POST /api/v1/workflows`，
X-N8N-API-KEY 认证）部署到真实实例——AI 生成 -> 编译 -> 部署闭环的落点。
纯标准库（urllib），零第三方依赖；HTTP/网络/非 JSON 响应显式 `ValueError`，
篡改 IR 在发出请求前就被 digest 拒绝。

```python
from runtime.deploy import deploy_to_n8n

created = deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="xxx")
# created = n8n 创建的 workflow（含服务端 id）
```

回归固化在 `tests/test_deploy.py`（urlopen mock，无真实网络）。
## 真实工作流批量验证（n8n 仓库自带样例，2026-08-18）

对 n8n 仓库 143 个工作流 JSON（workflow-sdk committed-workflows 14 + playwright
121 + editor templates 7）跑全链路（parse → check → compile → typed IR 校验 → digest）：

| 结果 | 数量 | 说明 |
|---|---|---|
| PASS | 130 | 全链路通过且**结构无损**，产物 `n8n-typed-ir` v2 严格校验 + SHA-256 digest 一致（含 AI 链工作流：IR v2 完整携带 `ai_*` 子连接） |
| 故意环 | 10 | 编辑器容忍环保存（运行时必败）；检出 = 正确行为 |
| Python 模式 | 1 | `pythonNative` Code 节点明确不支持（准确报错） |
| 畸形输入 | 1 | 空名节点 + 改名引用（n8n import 测试故意数据） |
| 真实坏代码 | 1 | **n8n 仓库自带语法错误 JS**（`1aaa;`）后端零校验放行，编译器 3:26 精确抓出 |

> **AI 支持状态（P1-1c，IR v2）**：`ai_languageModel/ai_tool/ai_embedding/ai_memory/ai_vectorStore`
> 等非 main 子连接完整携带进 IR（connections 每条带 `conn_type`），round-trip
> 无损还原回 `connections[源][conn_type]`——AI 链工作流已从 v1 的警告级
> PASS_AI_DROPPED（18 个）撤回 PASS。AI 子节点不参与 main 拓扑序
> （execution_order 按 main 拓扑节点集合做全排列断言，运行时由 agent 拉取）。
> manifest 字段 `ai_connections_dropped` 保留 v1 遗留字段名，语义为「携带的
> ai 边数」。

回归固化在 `tests/test_batch_matrix.py`（集合级断言，防漂移）；坏代码样例固化在
`tests/test_code_js.py::test_repo_bad_code_caught`。

## 测试

```bash
python3 -m unittest discover -s tests        # 全量（unit + node + n8n 矩阵）
python3 tests/coverage.py --threshold 85     # 覆盖率 + CI 门禁
```

分层（unit / integration(node) / matrix(n8n repo)）、`N8N_REPO` 环境变量、
覆盖率工具特性与治理基线见 [TESTING.md](TESTING.md)。当前基线：281 tests，
92.8% 覆盖率（2026-08-19，P1-2 AI 链 Code 静态分析后；
口径修复见 tests/test_coverage_tool.py）。
