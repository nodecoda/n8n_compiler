# n8n_compiler 架构审核报告（第五轮）

审核对象：`/home/dev/n8n_compiler`（ncoda→n8n 工作流编译器，Python 纯标准库；
源码 9,216 行：核心链 + typed_ir/manifest/cli + runtime + scripts + tests）
对照基准：`/home/dev/n8n` 运行时源码（本轮独立重读：public-api openapi 校验链、
workflowSettings.yml/node.yml schema、workflow-execute 执行序语义、langchain Code
节点双模式、NodeConnectionTypes 全集、mapConnectionsByDestination 反演）+ v4 审核结论
审核角色：编译器架构师（只读；未修改任何编译器业务代码，仅产出本报告）
审核日期：2026-08-19　性质：第五轮完整架构审核（重点：v4 后新增三条链——P1-1c
IR v2 ai_* 子连接携带/还原、P3-8 settings 携带、P1-2 langchain.code 静态分析）

> 纪律：所有基线数字为本轮**独立复跑**；所有关键断言附 file:line 证据（编译器侧 +
> n8n 侧）；v4 修复项逐条复核落地；真实实例数字为**记录转引**（本环境无远端访问权，
> 已在第七节标注）。

---

## 一、复跑基线（本轮实测）

| 项 | 结果 | 说明 |
|---|---|---|
| `python3 -m unittest discover -s tests` | **281 tests OK（6.2s）** | 独立复跑，非转引 |
| `python3 tests/coverage.py --quiet` | **COVERAGE 92.8% (2721/2932) ran=281 fail=0 err=0 skip=0** | 与 TESTING.md 基线逐位一致 |
| `test_batch_matrix`（143 文件重扫） | **130 PASS / 10 CYCLIC（8 cycle + 2 cycle+self_ref）/ 2 parse_error / 1 code_syntax_error** | 本轮独立重跑 `_run_all()` 实测分类 |
| **AI 子连接全矩阵守恒（新增实测）** | 18 个携带 ai_* 边的工作流（61 条 ai 边，10 种类型）**逐边多集比对 round-trip 0 失配**（(src, conn_type, port, to, index) 五元组含重复边） | 独立探针，见第五节 A |
| **settings 全矩阵守恒（新增实测）** | 52 个含非空 settings 的工作流中 46 个可编译，**round-trip 0 失配** | 独立探针，见第五节 B |
| **v1 文档装载（新增实测）** | 真 v1 形状（无 workflow.settings）**被 validate_typed_ir 拒绝**——P1-1（本轮新发现），见 P1-1 | 独立构造实测 |
| **langchain.code execute 变体（新增实测）** | `parameters.code.execute.code`（Main 输出）→ 误报 `code_syntax_error`——P2-1（本轮新发现），见 P2-1 | 独立构造实测 |
| **execute 模式在矩阵的暴露度** | 143 文件中 5 个 langchain.code 节点**全部**为 supplyData 形态，execute 形态 0 暴露 | 独立扫描 |

---

## 二、总体结论

**无 P0。新增 1 个 P1（v1 文档兼容装载契约失效——settings 被误列为必填字段）、
3 个 P2、4 个 P3。** v4 全部修复项逐一复核落地（upsert/凭据映射/隧道治理/覆盖口径
均在位）。三条审核链结论：

1. **P1-1c（IR v2 ai_* 携带/还原）——核心正确，全矩阵无损**：编译器与 typed_ir 的
   AI 子节点排除规则**无漂移**（矩阵 130 PASS + 三组构造边界探针 + IR 校验自身闭环）；
   ai 边方向/端口/输入槽编码与 n8n 反演语义逐位对齐；**但 v1 兼容装载链失效**
   （P1-1：`workflow.settings` 被列为必填，真 v1 文档无法装载，`if "settings" in
   workflow` 分支为死代码，既有 v1 测试未剥离 settings 因此测不到）。
2. **P3-8（settings 携带）——round-trip 正确，契约层有缺口**：全矩阵 settings 守恒
   0 失配；decompile 恒产出 settings 满足 REST required；但**编译器对 settings 零
   白名单/零值域校验**，而 n8n REST 用 additionalProperties:false 的闭白名单 +
   openapi 请求校验（P2-2），部署路径可被未知键/非法枚举 400 拒。
3. **P1-2（langchain.code 静态分析）——supplyData 通道正确，execute 变体盲区**：
   取源/工厂 mode/checker 通道均实证正确（坏语法 → code_syntax_error 通道正常继承）；
   **但 n8n 节点双模式（execute/supplyData）只覆盖了后者**，Main 输出形态的
   langchain.code 被误判无源 → 假 code_syntax_error，双分支并存时存在取错源+错 mode
   的静默错编译亚型（P2-1）。

分级汇总：**P0=0，P1=1（新），P2=3（新），P3=4（新）**；v4 遗留全部关闭
（P2-8 module-only 严格、checker 类型信号≈0 仍活且有意）。

---

## 三、与 v4 的 Delta

### 3.1 v4 修复项：全部复核落地 ✅

| v4 项 | 状态 | 本轮证据 |
|---|---|---|
| P1-1（AI 子连接携带，P1-1c） | ✅ 落地且正确 | `parser/workflow.py:162-190`（ai_connections 全携带、方向子→主）；`compiler/workflow.py:137-147`（conn_type 序列化）；`typed_ir.py:277-315`（白名单/to_port 放宽）；`runtime/decompile.py:110-128`（按 conn_type 分组还原） |
| P1-1 验证缺口 | ✅ 关闭 | 矩阵 130 PASS 撤回含 AI 链工作流；全矩阵独立探针 0 失配（见第五节 A） |
| P2-1（未注册 trigger → entry_keys 恒空） | ✅ 落地 | `compiler/workflow.py:26-43`（注册表 + "trigger" 命名启发式双保险）；回归 `test_unregistered_trigger_entry_key_fallback` |
| P2-2（deploy upsert） | ✅ 落地 | `runtime/deploy.py:51-77`（GET ?name= → PUT → POST；注释核对 n8n update 路由为 PUT 非 PATCH） |
| P2-3（凭据 name→id 映射） | ✅ 落地 | `runtime/deploy.py:85-153`（GET /credentials cursor 分页；缺失 name → ValueError 列清单，不静默降级；credential_map 显式跳过） |
| P2-4（隧道 pkill → Popen 持柄） | ✅ 落地 | `scripts/execute_deploy.py:82-83`；ids.json 恒落盘（:119-120）；per-scenario 容错 |
| P2-5（陈旧结果文件） | ✅ 落地 | `scripts/execute_deploy.py` 执行前清理 out_dir（文档 :17） |
| P3-1..P3-8 | ✅ 全部在位 | 丢失清单 README、settings 注释修正（`runtime/decompile.py:134-141`）、注册表覆盖矩阵等均复核 |
| P1-2（v4 后补，langchain.code） | ⚠️ 部分落地 | supplyData 通道正确；**execute 变体盲区（P2-1，本轮新发现）** |

### 3.2 新增 / 加剧项

| 项 | 类别 | 说明 |
|---|---|---|
| **P1-1（新）v1 文档兼容装载失效** | 缺陷 | `_WORKFLOW_FIELDS` 含 settings 且 required（typed_ir.py:37,126），与自身注释「settings 可选，v1 无此字段」(:35-36) 及「旧 v1 文档仍可装载」(:26-28) 矛盾；真 v1 文档实测被拒 |
| **P2-1（新）langchain.code execute 变体未取源** | 缺陷（边界） | n8n 节点双模式只覆盖 supplyData；Main 输出形态误报 code_syntax_error，双分支并存亚型可静默错编译 |
| **P2-2（新）settings 零 schema 契约校验** | 健壮性 | 编译器透传源 settings，n8n REST 闭白名单 + openapi 校验 → 未知键/非法枚举 deploy 400 |
| **P2-3（新）round-trip 守恒测试强度不足** | 验证缺口 | 单测按类型计数不校验边身份；fan-out/多输入槽无单测（代码实测正确，测试弱于声明） |
| **P3-1..P3-4（新）** | 文档/可维护性 | manifest 字段名遗留、浮空 AI 主节点入拓扑序、decompile docstring 过时、编译器可产出自身 loader 拒绝的 IR |

---

## 四、缺口分级表（P0–P3）

### P0
无。

### P1

**P1-1　v1 文档兼容装载契约失效：`workflow.settings` 被列为必填字段，真 v1 IR 文档无法装载 —— 缺陷**
- 证据（编译器侧）：
  - `typed_ir.py:37` —— `_WORKFLOW_FIELDS = {"id", "version", "entry_keys", "exit_key", "settings"}`；
  - `typed_ir.py:126` —— `_validate_fields(workflow, required=_WORKFLOW_FIELDS, ...)`：settings 属 required，缺失即 `ValueError("typed IR workflow missing required field 'settings'")`；
  - `typed_ir.py:35-36` —— 注释明言「settings 可选（v2 携带源 settings 原值，**v1 无此字段**）：缺失 -> decompile 回退」——设计与实现直接矛盾；
  - `typed_ir.py:26-28` —— 「旧 v1 文档（无 conn_type）仍可装载」的兼容声明（`_ACCEPTED_VERSIONS = {1, 2}`）；
  - `typed_ir.py:155-160` —— `if "settings" in workflow:` 分支：由于 required 恒在，此分支**恒真，成为死代码**（防御写成摆设）；
  - `runtime/decompile.py:142` —— `ir.get("workflow", {}).get("settings") or {"executionOrder": "v1"}` 回退：真 v1 文档进不了校验入口，回退仅对 v2 空 settings 生效，v1 路径实际不可达。
- 证据（测试盲区）：
  - `tests/test_compiler.py:285-301` —— `test_v1_document_without_conn_type_loads` 自称「真 v1 文档仍可装载」，但只剥离了 `conn_type`，**未剥离 `workflow.settings`**（v2 编译产物恒带 settings），因此测不到本缺陷；
  - 全测试目录无任何用例构造 `format_version=1` 且无 settings 的文档（grep 验证）。
- 证据（本轮实测）：
  - 构造真 v1 形状（format_version=1、删 settings、重算 digest）→ `validate_typed_ir` **拒绝**：「typed IR workflow missing required field 'settings'」；
  - 同文档保留 settings → 装载 OK。差异唯一变量即 settings 字段。
- 影响：任何 v1 时代产物（或手工 v1 文档）**全部**被拒绝装载；`_ACCEPTED_VERSIONS={1,2}` 的 v1 通道形同虚设；README.md:5「typed IR v2（v1 兼容装载）」文档承诺不成立。当前无生产 v1 产物则无即时损失，但契约声明与行为分裂是实缺陷。
- 修复路径（一行级）：`required=_WORKFLOW_FIELDS - {"settings"}`（typed_ir.py:126），使 :155 死分支复活；补真 v1 回归（v1 形状 = 无 settings + 无 conn_type + execution_order 含全部节点）；顺带修正 :35-36/:26-28 注释一致性。

### P2

**P2-1　langchain.code 仅覆盖 supplyData 模式：execute 变体（Main 输出）未取源 —— 缺陷（边界）**
- 证据（编译器侧）：
  - `parser/node_adaptors.py:38-46` —— `_code_source` 对 `@n8n/n8n-nodes-langchain.code` 只读 `parameters.code.supplyData.code`（+ 顶层字符串兜底），mode 恒 `"factory"`；`execute` 分支完全缺席；
  - `parser/node_adaptors.py:46` —— 两分支均不命中时返回 `("", "factory")` → `adapt_node`（:97-103）给空源错误契约「Code node has no JS source」→ `checker/validator.py:308-316` 上报 `code_syntax_error` → `compile_ast` 整体拒绝。
- 证据（n8n 侧）：
  - `packages/@n8n/nodes-langchain/nodes/code/Code.node.ts:269-311` —— `code` 参数为 fixedCollection，**同时声明 execute 与 supplyData 两个 value**（:286-288 提示「This code will only run and return data if a "Main" input & output got created」——Main 输出形态是 n8n 一等能力）；
  - `Code.node.ts:415-420` —— execute() 分支读 `code.execute.code`（缺失即抛 "No code for 'Execute' set"）；
  - `Code.node.ts:455,471-484` —— execute 用 `runCodeAllItems`（有 items 输入），supplyData 用 `runCode<Tool>()`（无 items）——两种 mode 的**语义不同**（后者才是 compiler 的 factory 语义）。
- 证据（本轮实测）：
  - 构造合法 Main 输出工作流（`parameters.code.execute.code = "return items.map(i => i.json);"`）→ `validate_workflow` 输出 `[('code_syntax_error', 'Pre')]` —— **合法工作流被误拒**；
  - 矩阵 143 文件：5 个 langchain.code 节点**全部** supplyData 形态，execute 形态 0 暴露（无回归保护）。
- 性质亚型（更重）：若节点 `parameters.code` 同时含 execute 与 supplyData（编辑器切换输出类型的历史残留）而实际接 Main 输出，编译器读 supplyData 分支 + factory mode → **取错源 + 错 mode 语义，且无编译错误 → 静默错编译**。
- 影响：Main 输出形态的 langchain.code（AI 工作流主链预处理/后处理常见用法）被假拒绝或静默错编译；`code_syntax_error` 通道对「空源」与「真语法错误」不区分，误导排障。
- 修复路径：`_code_source` 按节点实际输出连接类型分流——Main 输出（connections[src][main] 存在）读 `execute.code`、mode 用 runOnce* 语义；ai_* 输出读 `supplyData.code`、mode=factory；双分支并存时以实际连接为准；补 execute 变体回归。

**P2-2　settings 零 n8n schema 契约校验：源携带未知键/非法枚举时 deploy 400 —— 健壮性**
- 证据（编译器侧）：
  - `parser/workflow.py:281` —— `settings=data.get("settings") or {}` 原样透传；
  - `compiler/workflow.py:125-128` —— IR v2 携带原值；
  - `typed_ir.py:155-160` —— 仅校验「settings 是 dict + 键是字符串」，**无键白名单、无值形状、无枚举校验**。
- 证据（n8n 侧）：
  - `packages/cli/src/public-api/v1/handlers/workflows/spec/schemas/workflowSettings.yml:1-2` —— `type: object, additionalProperties: false`（闭白名单：saveExecutionProgress/saveManualExecutions/saveDataErrorExecution(enum all|none)/saveDataSuccessExecution(enum all|none)/executionTimeout/errorWorkflow/timezone/executionOrder/binaryMode/callerPolicy/callerIds/timeSavedMode/timeSavedPerExecution/redactionPolicy(enum)/availableInMCP/customTelemetryTags/credentialResolverId 等）；
  - `packages/cli/src/public-api/index.ts:280-291` —— public API 挂 `openApiValidatorMiddleware({ validateRequests: true })`：REST 创建/更新按该 schema 校验，未知键/非法枚举/值类型错 → 400；
  - `workflowCreate.yml:9-12` —— required 含 settings（decompile 恒产出，✓ 满足）；
  - CLI import 路径不校验 settings（`import/workflow.ts` 实体直存）——影响面为 **deploy/REST 路径专属**。
- 影响：ncoda 源（或手工编辑的 IR）settings 含未知键（如未来 n8n 新增键以外的任意键）或值域错误 → 部署 400；编译器无任何前置信号。n8n 侧「编辑器只写白名单键」的常态假设使实际触发率低，但 IR 是**可手工构造的公开契约**，契约层应至少 warning。
- 修复路径：typed_ir 或 parser 层对 settings 键做 n8n 白名单核对（未知键 → 编译 warning/issue，不阻断 round-trip）；值形状按 workflowSettings.yml 类型做宽松校验（enum 越界 → warning）。注意不要收死：n8n 白名单随版本演进，建议「未知键 warning」而非「拒绝」。

**P2-3　round-trip 守恒测试强度不足：按类型计数 ≠ 边身份守恒 —— 验证缺口**
- 证据：
  - `tests/test_decompile.py:222-237` —— `test_ai_edges_preserved_round_trip` 只断言 ai 边**类型 Counter** 相等（`edge_types()` 只取 `t` 类型），不校验 (from, conn_type, port, to, index) 身份；
  - `tests/test_decompile.py:42-56` —— 通用 round-trip 的 `_canonical_edges` 显式 `if conn_type != "main": continue`——**main 之外的边在通用等价断言中被整体排除**，ai 边守恒只靠上述弱断言；
  - 无任何单测覆盖 fan-out（同一 ai 端口多边，如 committed-workflows/0.json 的 SerpAPI→2 agent）与多输入槽 index 语义。
- 本轮独立实测（代码正确性）：18 个 AI 文件全量多集比对（含重复边计数）**0 失配**；0.json（16 条 ai 边 + fan-out）与 In_memory_vector_store_fake_embeddings.json（8 条 + 4 langchain.code）均在矩阵 PASS 且守恒。**结论：实现正确，测试弱于声明**。
- 修复路径：`test_ai_edges_preserved_round_trip` 升级为多集五元组断言（参考本报告第五节 A 探针）；新增 fan-out 单测（0.json 或手工构造）；新增 v1 装载 + round-trip 回归（并入 P1-1 修复）。

### P3

| # | 性质 | 证据 | 修复路径 |
|---|---|---|---|
| **P3-1** manifest.ai_connections_dropped 字段名遗留（v1 语义「丢弃数」，v2 实为「携带数」） | 文档/可维护性 | `manifest.py:62,108`；`typed_ir.py:353-363`（仅校验非负 int） | 注释/README 已记录（README.md:126）；IR v3 或 manifest 命名演进时改名 carried_ai_connections；勿在 v2 中改名（破坏 digest 兼容） |
| **P3-2** 浮空 AI 主节点（ai 边 to_node 无任何 main 边）进入 execution_order | 语义 wart（非缺陷，编译器/校验一致） | `parser/workflow.py:214-216`（注释假设「主节点必有 main 边」——实测不成立，见构造 Case C）；`compiler/workflow.py:46-57` 与 `typed_ir.py:318-333` 规则一致，该节点留拓扑序 | 若在意 execution_order 语义纯度，可把「ai 边 to_node 且无 main 边」并入排除集；但当前行为忠实于源且两处一致，优先级最低 |
| **P3-3** decompile 模块 docstring「typed IR v1 -> n8n Workflow JSON」过时 | 文档 | `runtime/decompile.py:1,94`（IR 已 v2） | 改「typed IR v1/v2」；同步 :16-17 settings 描述 |
| **P3-4** 编译器可产出自身 loader 拒绝的 IR（源 settings 非对象） | 健壮性缺口 | `parser/workflow.py:281`（透传）；`compiler/workflow.py:128`（照写）；`typed_ir.py:155-160`（装载才拒）——编译期无前置校验；本轮实测：`settings="not-an-object"` 源 → 编译 PASS → `validate_typed_ir` 拒绝 | parser 层对 settings 类型守卫（非 dict → 编译期 ValueError），或 compile 末尾自校验（`validate_typed_ir(自身)` 兜底，防任何序列化路径产出不可装载 IR） |

---

## 五、深层核对（三条审核链）

### A. P1-1c：IR v2 ai_* 子连接携带/还原

**A1 方向与编码对称性（n8n 原样 ⇄ IR ⇄ n8n 原样）**

n8n 真实导出约定（3 个真实文件实测）：`connections[子节点][ai_type][端口] = [{node: 主节点, type: ai_type, index: 主节点输入槽}]`。
- 例：`Workflow_ai_agent.json` —— `connections["E2E Chat Model"]["ai_languageModel"][0] → node="AI Agent"`（子→主）。
- 编译器编码：`parser/workflow.py:180-186` —— from_node=子节点、from_port=`main_N`（ai 类型端口下标）、to_node=主节点、to_index=edge.index（主节点输入槽）、conn_type=ai_type。与 n8n 反演语义逐位对齐：`mapConnectionsByDestination`（`packages/workflow/src/common/map-connections-by-destination.ts`）把 `[子][ai][i] = [{node:主, index:k}]` 反演为 `[主][ai][k] = [{node:子, index:i}]` —— **edge.index 即主节点该连接类型输入槽位**，编译器 to_index 编码/还原正确。
- 反编译还原：`runtime/decompile.py:110-128` —— 按 (from_node, conn_type, port) 分组写回 `connections[子][ai_type][port]`，边 `type: conn_type`，`index: to_index`。结构完全对称。
- **fan-out 极端形态**（同一 ai 端口多边：0.json SerpAPI Search Tool→2 个 agent、rag_starter.json Embeddings OpenAI→2 处）：parser 对端口内每条边各生成一条 Connection（同 from_port），decompile 按序 append —— 顺序与多集守恒（见 A3 实测）。
- **多端口 ai 连接**（同一子节点同一 ai_type 多端口）：矩阵实测 0 例；编码 `main_N` 已覆盖（typed_ir `_BRANCH_PORT` `^main(_[0-9]+)?$` 允许，decompile `_port_index` 还原），无盲区。
- **to_index 实测语义**：0.json 多工具 agent 5 条 ai_tool 边 index 全为 0 —— n8n 当前对同型多子连接以列表槽位 0 挂载；编译器原样携带，忠实。

**A2 `_main_topology_nodes` ⇄ `_ai_only_subnodes` 防漂移**

两侧规则逐字段等价（编译器侧 `compiler/workflow.py:46-57`；typed_ir 侧 `typed_ir.py:318-333`）：
`main_participants = from ∪ to(main 边)`；`excluded = ai_sources − main_participants`；expected = 全部节点 − excluded。本轮构造 3 组边界探针实测一致：
- Case A 纯 AI 子节点（无 main 边）→ 两侧均排除（LLM 出拓扑序）✓
- Case B 双参与者（子节点兼有 main 边与 ai 边）→ 两侧均保留 ✓
- Case C 浮空 AI 主节点（ai 边 to_node 无 main 边）→ 两侧均保留（进入拓扑序，P3-2）✓

且 IR 校验自身即闭环：`typed_ir.py:336-350` 对 execution_order 做 main 拓扑节点全排列断言，任何一侧漂移都会在 `validate_typed_ir` 拒绝——矩阵 130 PASS 已隐含全量验证。**结论：无漂移。**

**A3 round-trip 守恒（独立实测，非转引）**

对全部 18 个携带 ai_* 边的工作流做「多集五元组」守恒（含重复边计数）：
`(src, conn_type, port_index, to_node, to_index)` —— **18/18 零失配**（含 0.json 16 条、fake_embeddings 8 条、RAG 6.json 3 条）。v4 报告的「真实实例 8/8 边 + execute exit=0」为记录转引（本环境无远端）。

**A4 v1 文档兼容装载：不成立（P1-1）**

真 v1 形状（format_version=1、无 settings、无 conn_type、execution_order 含全部节点）实测被 `typed_ir.py:126` 拒绝。conn_type 缺省与 execution_order 语义对 v1 均兼容（v1 文档无 ai 边 → expected=全部节点，与 v1 编译产物一致）；**唯一阻断项即 settings 必填**。

### B. P3-8：settings 携带

**B1 round-trip 守恒（独立实测）**：52 个含非空 settings 的矩阵文件中 46 个可编译，decompile 还原后 settings **0 失配**（含 executionOrder v1/v2 混存）。`test_settings_round_trip_restored` / `test_settings_default_v1_when_absent` / `test_settings_carried_in_ir_v2` / `test_workflow_settings_must_be_object` 四回归均在位。

**B2 REST 契约一致性**：
- 满足面：decompile 恒产出 settings（`runtime/decompile.py:142`），`workflowCreate.yml:9-12` required 含 settings ✓；还原字段白名单与 `node.yml:1-2` additionalProperties:false 兼容（decompiler 仅发 name/type/typeVersion/position/parameters/credentials + onError/retry*，全在白名单）✓；
- 缺口面：settings 值域无校验（P2-2）；`binaryMode`/`credentialResolverId` 为 n8n「派生/忽略」字段，携带无害但属噪音。

**B3 执行序语义复核**：`packages/core/src/execution-engine/workflow-execute.ts:199-201`（`isLegacyExecutionOrder: settings.executionOrder !== 'v1'`）、`:441`（v1→unshift）、`:964`（v2→forceInputNodeExecution）——「源 settings 优先 + v1 兜底」的确定性选择与 n8n 语义一致；缺失时 n8n 按 v2 处理而编译器兜底写 v1，为**保守显式**（非降级覆盖，v4 的 P3-8 边界已收口）。

### C. P1-2：langchain.code 静态分析

**C1 取源分流**：`parameters.code.supplyData.code` 与 n8n 当前导出形状逐位一致（`Code.node.ts:471-484` 运行时 `getNodeParameter('code') as { supplyData?: { code: string } }`）；旧版字符串兜底防御性保留。**execute 变体缺失 → P2-1**。

**C2 工厂模式语义**：`code/js_static.py:31-38` `_MODE_HINTS["factory"]`（"returns a component instance (no items input)"）与 n8n supplyData 语义一致（`Code.node.ts:455` runCode<Tool> 无 addItems；execute 才有 addItems + items）。「工厂 = 无 items 输入」的 hint 只对 supplyData 成立——对 execute 变体错误（P2-1 联动）。

**C3 NewExpression→OBJECT 对 base.code 的既有行为影响**：
- 变更点 `code/js_ast.py:329-333`（`return new X()` → OBJECT，props 静态未知）。
- 消费方影响面核对：CodeNode 契约 output → `output_types`（`ast_nodes/node_decls.py:191`）→ checker `_shape_unknown`（`checker/validator.py:255-263`）：**空 props object 仍判为「形状未知」**（`info.is_object() and not info.properties → True`），不会触发 `source_field_missing` 假阳性——**对 base.code 零行为回归**（281 tests 全绿佐证）。OBJECT 比 ANY 更精确仅对 IR 消费方有益。
- 唯一语义注意点：`return new Date()` 一类（值即对象实例）与「返回构造产物」都被归 OBJECT，无属性猜测——保守且正确。

**C4 checker code_syntax_error 通道继承（实测）**：坏语法 langchain.code → `validate_workflow` 输出 `code_syntax_error` 且 node_id 正确指向该节点 —— 通道经 `isinstance(node, CodeNode)`（`checker/validator.py:308-316`）+ `node_class_for`（kind=CODE → CodeNode，`ast_nodes/mappings.py:41-57`）正确继承。回归 `test_langchain_code_bad_syntax_caught` 在位。**结论：通道正确。**（缺陷仅在 P2-1 的「空源伪装成语法错误」用例。）

---

## 六、优先建议排序

| 序 | 建议 | 级别 | 工作量 | 影响 |
|---|---|---|---|---|
| 1 | 修复 P1-1：`required=_WORKFLOW_FIELDS - {"settings"}`（typed_ir.py:126）+ 补真 v1 回归（无 settings + 无 conn_type + execution_order 全节点） | P1 | 低（1 行 + 1 测试） | 恢复声明中的 v1 兼容装载；复活 :155 死分支；使 decompile v1 回退路径可达 |
| 2 | 修复 P2-1：`_code_source` 按节点实际输出连接分流 execute/supplyData + 双分支并存以连接为准；补 execute 变体回归 | P2 | 中 | 消除合法工作流假拒绝与静默错编译亚型；关闭 P1-2 剩余盲区 |
| 3 | 修复 P2-3：round-trip 守恒断言升级为多集五元组 + fan-out/多输入槽单测 | P2 | 低 | 把「实测正确」固化为「测试保证」，防未来回归 |
| 4 | P2-2：settings 键/值域对照 workflowSettings.yml 白名单 warning（不阻断） | P2 | 中 | deploy 400 前给出编译期信号；保持未来 n8n 新键兼容 |
| 5 | P3-4：parser settings 类型守卫或 compile 末自校验 | P3 | 低 | 编译器不再产出自身 loader 拒绝的 IR |
| 6 | P3-1/3-2/3-3：注释/文档收口（manifest 字段名、decompile docstring、浮空主节点语义） | P3 | 低 | 文档与实现一致 |

---

## 七、置信度与证据边界

- **高置信（独立实测）**：281 tests OK / 92.8% 覆盖率 / 130 PASS 矩阵分类；18 个 AI 文件多集守恒 0 失配；46 个 settings 文件守恒 0 失配；v1 无 settings 装载被拒；execute 变体假 code_syntax_error；漂移探针 A/B/C 一致。
- **记录转引（本环境无远端访问权，未复核）**：v4 记录的 nodecoda-production 真实实例数字（create 7/7、upsert 7/7、execute 7/7、RAG 8/8 ai 边、fake-embeddings 部署 exit=0、凭据命中/缺失双路径）。这些不在本轮实测范围内。
- **静态推断（未运行 n8n 本体）**：P2-2 的「未知 settings 键 → REST 400」基于 `openApiValidatorMiddleware(validateRequests:true)`（`public-api/index.ts:280-291`）+ `workflowSettings.yml additionalProperties:false` 的代码路径推断，未在本环境对真实实例发请求实证；CLI import 路径不校验为 `import/workflow.ts` 代码阅读结论。
- **暴露度边界**：P2-1 的 execute 变体在 143 文件矩阵 0 暴露；P1-1 的 v1 产物是否真实存在于生产未可知（项目为新工程）。两者评级已按「契约声明/能力边界」而非「当前流量」定级。

---

## 八、修复记录（第五轮审核后实施批次，2026-08-19）

审核为纯只读；审核结论交付后按第六节优先级实施了第一批修复（全部先红后绿）：

| 项 | 修复 | 状态 |
|---|---|---|
| P1-1 | `typed_ir.py:126` `required=_WORKFLOW_FIELDS - {"settings"}`；`test_v1_document_without_conn_type_loads` 升级为真 v1（剥离 settings），断言装载不注入 settings | ✅ 绿 |
| P2-1 | `_code_source` 增 `branch` 分流（execute → `parameters.code.execute.code` / supplyData → `supplyData.code`）；`parser/workflow.py` 新增 `_active_code_branch` 按实际输出连接判定（main 出边 → execute，仅 ai 出边 → supplyData，双分支以 main 为准）；mode：execute→runOnceForAllItems / supplyData→factory | ✅ 绿 |
| P2-4 | `node_type.py` 注册 `@n8n/n8n-nodes-langchain.toolCode`（CODE, 0入/0出）；`_toolcode_source` 顶层 jsCode 取源 mode="tool"，python → UnsupportedSourceError；`js_static.py` 增 `tool` mode hint；hitl-wrapped-tool/mcp-trigger 实样本 js 分析生效（config.js + js_ast 在位） | ✅ 绿 |
| P2-3 | `test_decompile.py` 新增 `_canonical_edges_all` 多集五元组；`test_ai_edges_preserved_round_trip` 升级多集守恒；新增 fan-out + 多输入槽单测 | ✅ 绿 |
| P2-2 | `compiler/workflow.py` 新增 `check_workflow_settings`（17 键白名单 + 类型/枚举值域 warning）；`CompiledWorkflow.warnings`；CLI compile 打 stderr 不阻断、不污染 stdout IR | ✅ 绿 |
| P3-4 | `parser/workflow.py` settings 非对象 parse 期显式 ValueError（不再产出自身 loader 拒绝的 IR） | ✅ 绿 |
| P3-1/2/3 | manifest 字段语义已在 v4 注释 + README:126 收口（复核在位）；`_ai_only_subnodes` 补浮空 AI 主节点语义注释；`decompile.py` 文档串更新为 v2/v1 兼容口径 | ✅ |

**终验（本轮实测）**：290 tests OK（+9）· COVERAGE 92.4% (2799/3028)（CI 门禁 90% 之上）·
矩阵 130 PASS / 10 CYCLIC 保持 · hitl-wrapped-tool + mcp-trigger 实样本 toolCode 节点
`config.js`+`js_ast` 在位（此前 GENERIC 无分析）。

---

## 附：n8n 侧关键证据行（本轮独立核对）

- `packages/workflow/src/interfaces.ts:2806-2820` —— NodeConnectionTypes 全集：main + 12 个 ai_* 类型（ai_agent/ai_chain/ai_document/ai_embedding/ai_languageModel/ai_memory/ai_outputParser/ai_retriever/ai_reranker/ai_textSplitter/ai_tool/ai_vectorStore）；编译器 `startswith("ai_")` 白名单覆盖全集且未来安全
- `packages/workflow/src/common/map-connections-by-destination.ts` —— 连接反演：edge.index = 目标节点输入槽位（ai 边同理），确认 to_index 语义
- `packages/@n8n/nodes-langchain/nodes/code/Code.node.ts:269-311,415-420,455,471-484` —— langchain.code 双模式（execute/supplyData）、取源字段、模式语义
- `packages/cli/src/public-api/v1/handlers/workflows/spec/schemas/workflowSettings.yml:1-2` —— settings 闭白名单（additionalProperties:false）
- `packages/cli/src/public-api/v1/handlers/workflows/spec/schemas/workflowCreate.yml:9-12` —— required 含 settings
- `packages/cli/src/public-api/v1/handlers/workflows/spec/schemas/node.yml:1-2` —— 节点字段闭白名单（decompile 产物兼容）
- `packages/cli/src/public-api/index.ts:280-291` —— openApiValidatorMiddleware validateRequests:true（settings 未知键 → 400 依据）
- `packages/core/src/execution-engine/workflow-execute.ts:199-201,441,964` —— executionOrder !== 'v1' → v2 语义（settings 兜底语义复核）

## 附：编译器侧关键证据行（本轮复核）

- `typed_ir.py:26-28,35-37,126,155-160,277-315,318-333,336-350` —— IR v2 契约与校验
- `parser/workflow.py:162-190,214-218,281` —— ai 边携带/方向/浮空假设/settings 透传
- `compiler/workflow.py:26-43,46-57,125-128,137-147` —— entry_keys/ai 子节点/拓扑序/settings/连接序列化
- `runtime/decompile.py:1,94,110-128,134-142` —— 还原与 settings 兜底
- `parser/node_adaptors.py:28-60,97-103` —— Code 取源分流与空源契约
- `code/js_ast.py:329-333`；`code/js_static.py:31-38` —— NewExpression→OBJECT 与 factory hint
- `checker/validator.py:255-263,308-316` —— 形状未知判定与 code_syntax_error 通道
- `ast_nodes/mappings.py:41-57`；`ast_nodes/node_type.py:97-99` —— langchain.code → CODE → CodeNode
- `manifest.py:62,108`；`tests/test_compiler.py:285-301`；`tests/test_decompile.py:42-56,222-237` —— manifest 字段名与测试盲区

---

## 十、Leader 复核附录（第五轮，追加）

> 本附录由审核 leader 在收到 architect 报告后独立复核追加：①对 architect 三条核心
> 结论的实证复核结果；②architect 报告遗漏的 toolCode 盲区（P2）。仅追加证据与结论，
> 未修改报告主体。

### 10.1 leader 实证复核（独立复跑，非转引）

| architect 结论 | leader 复核 | 证据 |
|---|---|---|
| 基线 281 OK / 92.8% / PASS=130 | ✅ 一致 | leader 独立复跑：`unittest discover`→281 OK；`coverage.py --quiet`→92.8% (2721/2932)；`test_batch_matrix`→OK（PASS 集合精确断言 130） |
| P1-1 settings 必填切断 v1 装载 | ✅ 属实（实测复现） | 真实编译产物改造：v2 原样 ACCEPTED；`format_version=1` + 删 settings + 删 conn_type + 重算 digest → `ValueError: typed IR workflow missing required field 'settings'`；同文档保留 settings → ACCEPTED。**settings 为唯一变量**。代码层：`typed_ir.py:37,126`（required）vs `:35-36`（注释声称可选） |
| P2-1 execute 变体假拒绝 | ✅ 属实（实测复现） | `parameters.code.execute.code`（合法 JS）→ compile FAIL rc=1 `validation error: Code node "LcCode" JS syntax error: Code node has no JS source`；同构 supplyData 变体 → rc=0。`parser/node_adaptors.py:38-46` 只读 supplyData 分支 |
| P2-2 settings 零 schema 校验 | ✅ 代码层成立（未发真实请求） | `workflowSettings.yml additionalProperties:false` + `public-api/index.ts:280-291` validateRequests:true 路径阅读确认；与 architect「静态推断」置信度标注一致 |

### 10.2 新发现：toolCode（`@n8n/n8n-nodes-langchain.toolCode`）JS 未分析 —— **P2（缺陷）**

**architect 报告未覆盖**（全文字符串检索 0 命中）。与 P2-1 同族：AI 链内 JS 一等公民
能力覆盖不完整。区别：P2-1 是已注册节点（langchain.code）的取源分流缺一分支；toolCode
是**整类节点未注册**（落 GENERIC 泛型透传），其工具 JS 完全绕过静态通道。

- **注册缺失（编译器侧）**：`ast_nodes/node_type.py:83-99` REGISTRY 无 toolCode 条目 →
  `spec_for` 落 `GENERIC_SPEC`（:101-104）→ `parser/workflow.py:132-134` 的 acorn 批量
  通道按 `kind != CODE` 跳过 → 无语法检查、无 shape 提取、无 ESTree 进 IR。
- **n8n 官方契约（对照基准）**：`packages/@n8n/nodes-langchain/nodes/tools/ToolCode/
  ToolCode.node.ts:203` `inputs: []`、`:205` `outputs: [NodeConnectionTypes.AiTool]`
  （纯子节点：0 main 入 / 0 main 出，输出走 ai_tool 子连接）；取源为**顶层**
  `parameters.jsCode`（language='javaScript'）/ `parameters.pythonCode`（:64-69，
  非 supplyData 嵌套，与 langchain.code 不同）；`:73` 运行时走
  `JsTaskRunnerSandbox.runCodeForTool`（工具签名 `(query) => result`）。
- **实测（本轮）**：`packages/testing/playwright/workflows/hitl-wrapped-tool.json`
  编译 PASS（rc=0）；IR 中 2 个 toolCode 节点落 `kind: generic`、params 含 `jsCode`、
  `config.js` 缺失（**无静态分析**）；ai 边仍正确携带 4 条（1× ai_languageModel +
  3× ai_tool）——**连通性无损，缺口仅在 JS 分析**。
- **暴露度**：n8n 仓库 8 文件 11 节点（playwright fixtures）；其中 6 文件
  （hitl-wrapped-tool + 5× mcp-trigger）**在 130 PASS 矩阵内**——这些文件以泛型透传
  PASS，工具 JS 语法错误在编译期静默（比 P2-1 的假拒绝更隐蔽：P2-1 报错、toolCode
  不报错）；真实实例 9 节点（记录转引，v4 实测口径）。
- **影响**：toolCode 是 AI 链「自定义工具」的标准承载点（文档
  `docs.n8n.io/.../n8n-nodes-langchain.toolcode/`），工具 JS 是 ncoda foreign code
  语义的核心消费方——「declared types are authoritative」模型下，未分析的 JS 即未
  校验的类型承诺。属 P2（能力边界缺陷，非当前矩阵红）。
- **修复路径（与 P2-1 同一批次，工作量低）**：
  1. `node_type.py` 注册 `"@n8n/n8n-nodes-langchain.toolCode": NodeSpec(NodeKind.CODE, 0, 0, ShapeKind.ANY, "Code Tool")`（0 main 入/出，纯 ai_tool 子节点——拓扑排除规则已天然处理）；
  2. `parser/node_adaptors.py:_code_source` 增 toolCode 分支：顶层 `jsCode`（language=javaScript）→ mode `"tool"`（新 mode hint：`(query) => result` 工具签名）；`pythonCode` → 沿用 UnsupportedSourceError 语义；
  3. `code/js_static.py:_MODE_HINTS` 增 `"tool"` 条目；
  4. 补回归：toolCode 坏语法 → code_syntax_error；toolCode 好语法 → ESTree 进 IR + ai_tool 边守恒（复用 hitl-wrapped-tool 或构造等价小工作流）。

### 10.3 优先级更新（合并后）

| 序 | 项 | 级别 | 工作量 |
|---|---|---|---|
| 1 | P1-1：`required -= {"settings"}` + 真 v1 回归（architect 原结论，leader 实测确认） | P1 | 低 |
| 2 | P2-1 + **P2-4(toolCode)**：取源分流补 execute + 注册 toolCode 走同通道（一并向「AI 链 JS 全覆盖」收口） | P2 | 中 |
| 3 | P2-3 round-trip 多集断言 / P2-2 settings 白名单 warning / P3 批 | P2/P3 | 低-中 |
