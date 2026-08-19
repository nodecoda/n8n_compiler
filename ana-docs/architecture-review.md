# n8n_compiler 架构审核报告

审核对象：`/home/dev/n8n_compiler`（n8n 工作流 AOT 编译器前端，Python 纯标准库）
对照基准：`/home/dev/coze_compiler` 分层规格 + `/home/dev/n8n` 运行时语义
（`ana-docs/n8n-runtime-chain.md`、`n8n-compiler-alignment.md`）
审核日期：2026-08-18　审核角色：编译器架构师（只读，未改任何代码）

> 本文所有基线数字均为**本次实际复跑验证**（不是转引 README）：
> - `python3 -m unittest discover -s tests` → **183 tests OK**（4.3s）
> - `python3 tests/coverage.py --quiet` → **COVERAGE 93.5% (1992/2131)** fail=0 err=0 skip=0
> - 143 工作流矩阵独立重跑 → **130 PASS / 10 环检出 / 3 其他**（8×`cycle_detected`、2×`cycle_detected+self_referencing_edge`；3 其他 = 1×pythonNative `JSInfraError`、1×空名 `ValueError`、1×坏 JS `code_syntax_error`）
> - acorn 版本对拍：`package.json` 锁定 **acorn 8.14.0**，n8n 侧 `@n8n/task-runner` 与 `@n8n/workflow-sdk` 同为 **8.14.0**（grep 确认）

---

## 总体结论

**结构健康，无已证实 P0 级正确性缺陷。** 分层单向无环、外部 gate 显式失败、矩阵与
覆盖率基线可信、治理纪律（TESTING.md）在同规模项目中属优秀。审核发现 **4 个 P1
结构性问题**（其中 1 个是 IR 反序列化丢依赖信息的真缺口，1 个是 Switch 路由数硬编码
导致的边界 sink 丢失风险），以及 13 个 P2 建议。没有发现会导致 130 个 PASS 工作流
产出错误 IR 的缺陷；两个最接近正确性风险的项（Switch>4 路由、acorn 选项对拍未固化）
如实归入 P1 并说明触发条件。

---

## 一、分层与依赖方向

### 1.1 依赖图（AST 静态扫描全量 import 边，35 个模块）

**无环**（DFS 三色验证通过）。跨层边全部单向，方向如下（"→" = 依赖方 → 被依赖方）：

```
parser.workflow      → ast_nodes / scope / type_system / values / parser.expression
parser.node_adaptors → ast_nodes / code(contract)
checker.validator    → ast_nodes / type_system / values
compiler.dependency  → ast_nodes / values
compiler.workflow    → ast_nodes / checker / compiler.dependency / manifest / typed_ir
cli                  → parser / checker / compiler
ast_nodes.node_decls → code.contract / type_system          （CodeNode 一等公民的代价）
ast_nodes.mappings   → code.contract / type_system
```

结论：核心链 `parser → ast_nodes/type_system/scope → checker → compiler → typed_ir`
单向成立，与规格一致。

### 1.2 三处需要显式确认的耦合

1. **`compiler/workflow.py:13-16` → `typed_ir` + `manifest`（"顶层"被编译器引用）。**
   这是分层上唯一"向上"的边，但与 coze 基准完全同构
   （`/home/dev/coze_compiler/compiler/workflow.py:21-22` 同样 import `manifest`/`typed_ir`），
   属规格继承而非漂移：`compile_ast` 直接产出 `CompiledWorkflow` 文档，序列化格式
   与编译管线耦合是设计选择。代价：未来 IR v2 演进时 compiler 需同步改；建议把
   "IR 装载/校验"与"IR 产出"在语义上视为同一契约的两端，IR_VERSION 演进时一起升。
2. **`ast_nodes/node_decls.py:8-13` → `code.contract`。** CodeNode 持有 `StaticContract`，
   AST 层引用 JS 子系统类型。无环（`code/` 是叶子：`contract` 零依赖、`js_parser` 仅
   stdlib、`js_ast` 仅依赖 contract），但 import `ast_nodes.node_decls` 会连带执行
   `code/__init__.py`（引 js_parser/js_ast，均为纯 stdlib，**不会在 import 期启动
   Node 进程**——已验证）。可接受；若未来要把 AST 层独立成库，需把 `code.contract`
   下沉或接口化。
3. **`ast_nodes/nodes.py:26-28` `TYPE_CHECKING` 引 `scope`。** 类型级前向引用，
   `from __future__ import annotations` 下无运行时解析，`scope/` 亦不反依赖
   ast_nodes → 无运行时环。安全。

### 1.3 mappings.py 与 node_decls 的分派

分派链：`node_class_for(n8n_type)` → `spec_for()` 查 `REGISTRY`（`node_type.py:74-100`）
取 `.kind` → `_KIND_TO_CLASS` 映射 → 未命中落 `GenericNode`。链干净、有兜底。
**一个双源漂移风险**：`REGISTRY` 里的 `NodeKind` 与各子类 `KIND` ClassVar 是两份事实
（coze 用 `NODE_CLASS_BY_TYPE` + `KIND` 单源生成映射）。若未来有人把 REGISTRY 中某
type 的 kind 改成未注册 class 的值，会**静默落 GenericNode**（node_type.py:103,107）。
建议：由子类 `KIND` 生成式构建 `_KIND_TO_CLASS` 或加一致性测试（见 P2-2）。

### 1.4 code/（JS 子系统）与其他层的关系

`code/` 是全项目唯一有外部 gate 的层，隔离干净：

- `js_parser.py:23-24` `JSInfraError` 定义；`:51-59` `find_node`（NODE 环境变量 → PATH
  查找 → 缺失 raise）；`:65-89` `_run_bridge` 全部失败路径（缺脚本/超时/OSError/
  非零退出/坏 JSON）都 raise `JSInfraError` —— **不静默降级**，符合"外部 gate 不可用
  必须明确失败为基础设施错误"。
- `js_parser.py:110-126` `parse_js_batch` 一次进程解析全部 Code 节点（启动开销摊薄，
  设计正确）；`parser/workflow.py:85-107` `_precompile_code_nodes` 在 parse 入口批量
  调用。
- `scripts/js_parse.mjs`：acorn `ecmaVersion:'latest'`、`sourceType:'module'`、
  `allowReturnOutsideFunction:true`、`allowAwaitOutsideFunction:true` —— 对齐 n8n
  Code 节点"async 函数体"语义（runtime-chain §4），import/export 语法级可解析、在
  `js_static.py:50-67` 经 `reject_module_syntax` 前置为编译错误（与 n8n 运行时行为
  一致且更严，alignment 表 #9 ✅）。

---

## 二、边界与接口

### 2.1 NodeDecl 基类字段（`ast_nodes/nodes.py:46-90`）

`key/n8n_type/node_type/name/type_version/position/parameters/input_types/
output_types/input_sources/output_sources/error_policy/credentials/parent_key/scope`
——字段集合理：`input_types/output_types` 以**端口名**为键（"main"/"main_0"/"main_1"，
与 n8n 输出端口索引约定一致），`input_sources` 统一承载"表达式引用 + Code 依赖"两种
绑定。`input_port_count` 默认 `0 if not input_types else 1`，Merge 重写为 2
（`node_decls.py:58-60`），覆盖点明确。`output_sources` 恒空（仅 checker 有
`exit_node_has_output_sources` 守卫）——保留位，无实义，P2 可考虑删除或文档化。

### 2.2 Connection 模型（`ast_nodes/connection.py:16-43`）

`from_node/from_port/to_node/to_port/to_index` 完整。关键点：

- **`to_index`（目标输入端口索引）**：`parser/workflow.py:139` 读 `edge.get("index", 0)`；
  `identity` 含 to_index（`:31-33`）——同源连同一目标不同输入端口不再误判 duplicate；
  IR 序列化携带（`compiler/workflow.py:90-96`）、白名单校验（`typed_ir.py:250-254`）
  缺省即 0（向后兼容，`test_alignment_bounds.py:168-177` 固化）。与 n8n
  `workflow-execute.ts:519` 的 `main[connectionData.index]` 写槽语义对齐
  （runtime-chain §3.7，alignment 表 #5 🔧 已修）。
- **`to_port` 恒 "main"**：n8n 输入恒为单一 main，Merge 等多输入靠多条边 + to_index
  汇入同一输入 —— 建模正确（IR 校验 `to_port != "main"` 即拒，`typed_ir.py:262-263`）。
- 端口名宽容策略（`parser/workflow.py:48-61`）：注册表声明 output_ports>1 才命名
  main_{i}；实际连接出现更高索引时放行为 main_{i} —— 与 n8n"编辑器保存零校验"对齐。

### 2.3 ParsedRef / Reference / FieldInfo 引用模型

自洽的三层：`parser/expression.py` 产出**引用意图**（ParsedRef：NODE/INPUT/GLOBAL/
UNKNOWN），`parser/workflow.py:210-236` 按图上下文**绑定为最终 Reference**
（$json/$input 需入边唯一上游；$node 直绑；GLOBAL 带 variable_type），
`values/reference.py` 定义 Reference/Source/FieldInfo 三容器。`$node` 访问器纪律
（`expression.py:79-104`：只认 `.json`/`.output` 数据访问器，`.params/.body/.binary`
等标 UNKNOWN 不误绑）是防误报的关键正确性决策，有回归测试
（`test_expression.py::TestNodeAccessorDiscipline`，alignment 表 #12/#13 🔧）。

### 2.4 StaticContract 权威性（contract vs js_ast 双轨）

`code/__init__.py` + `CodeNode` docstring（`node_decls.py:74-115`）明确 **contract 是
权威**、js_ast 是可选负载（供下游消费）。方向正确。两个敞口：

- `typed_ir.py:164-176` 对 `config.js` 的校验很浅：只查 `effect/runtime` 是字符串，
  `deps/output/errors/warnings` 均未类型校验 —— 白名单不完整（P2-7）。
- 无"contract == derive(js_ast)"不变量测试：双轨在编译期同源，但 IR 反序列化后两者
  可能漂移（防篡改只靠 digest 覆盖，不靠语义不变量）（P2-8）。

### 2.5 ⚠️ P1：`load_typed_node` 丢弃 input_sources（IR→AST round-trip 真缺口）

`ast_nodes/mappings.py:76-101`：反序列化只回填 `input_types/output_types`，
**不回填 `input_sources/output_sources`**。coze 同款加载器明确回填
（`/home/dev/coze_compiler/ast_nodes/nodes.py:271-272`，注释"使运行时无需维护并行的
node dict 表示"）。后果：任何以 `load_typed_node` 消费 IR 的下游（未来的 runtime
adapter）拿到的 NodeDecl 丢失全部依赖/引用信息。当前无运行时（`runtime/` 为空目录、
README:24 声明 P1），所以暂无线上受害者，但这是**结构级欠账**；round-trip 测试
（`test_type_roundtrip.py:108-170`）的用例 dict 全部不含 input_sources，缺口无回归网。
**改法**：在 `load_typed_node` 末尾补
`node.input_sources = [FieldInfo.from_dict(s) for s in node_dict.get("input_sources", [])]`
（output_sources 同理），并加一条带 input_sources 的 round-trip 测试。

---

## 三、表达性与正确性风险

### 3.1 表达式 UNKNOWN 降级策略（设计正确，两点精度损失）

- **降级不丢信息**：复杂表达式 → `ParsedRef(UNKNOWN, raw)` 保留原串；原始参数 JSON
  完整留在 `config.parameters`。下游运行时按 n8n "`=` 前缀即表达式"语义（runtime-chain
  §2）照常求值 —— 静态分析只损失精度，不损失运行时正确性。`=abc` 按字面量处理是
  **有意安全侧**（alignment 表 #7 ⚠️）。
- **精度损失 a**：模板串只提取**首个**内嵌引用（`expression.py:188-195`），
  `{{ $json.a }}-{{ $node["X"].b }}` 的 $node 引用不进静态依赖（P2-4 已列）。
- **精度损失 b**：`parse_value` 返回的 `_dynamic` 标志被丢弃
  （`parser/workflow.py:181`）——IR 无"该字段含未静态分析表达式"标记。运行时反正按
  原始串求值，无正确性影响，但下游无法从 IR 得知哪些字段是静态不可信的（P2-4）。

### 3.2 $input / $node 访问器纪律

`$input` 覆盖 `all()/first()/item` 三形态（`expression.py:128-157`），**缺 `last()`
及 `$input.all()` 无下标等变体** → 落 UNKNOWN（安全方向，不误绑）。
`$('X')` 表达式形态未识别（alignment 表建议 #4，误判风险低可补）。Code 内
`$("X")`/`$items("X")`/`$item("X", i)` 调用形态已支持（`js_ast.py:122-137`）。
JS 侧 `items.map(...)` 方法调用边界不记为依赖、`x.foo`（参数）不记依赖
（`js_ast.py:141-147`）——作用域隔离正确。

### 3.3 checker type_mismatch 可赋性矩阵 —— **结构完整但几乎全潜伏**

矩阵（`checker/validator.py:178-197`）：any 万能；同型；STRING 目标收 NUMBER/BOOLEAN；
NUMBER/BOOLEAN 目标收 STRING（运行时可转）；OBJECT/ARRAY 目标收 STRING；ARRAY 递归
元素、双方元素都 any 时放行。方向保守合理（BOOLEAN 目标不收 NUMBER —— JS 里 0/1 →
boolean 虽可转，但静态拒掉不误杀真实数据流）。

**但实际可达性几乎为零**：目标侧 `input_types` 绝大多数是 `{main: any}` 或空 props
object（`_type_at_path` 对 any/空 object 返回 None，`validator.py:162-170`）→
`target_type` 恒 None → `type_mismatch` 分支在**真实工作流路径上从不触发**。
`source_field_missing` 仅在 CodeNode 静态 OBJECT 输出形状（非空 props）下可达
（`test_checker.py:41` 即此路径）。`test_checker_coverage.py:93-107,171-216` 全部用
**手造 AST + 显式 TypeInfo** 驱动，验证的是规则逻辑而非真实信号。
诚实结论：这是"如实反映 n8n 动态类型"的设计，但当前**类型检查近乎装饰** —— 文档
应明说（P2-1），或后续补节点参数 schema 让矩阵真正通电。

### 3.4 拓扑序（`compiler/workflow.py:34-61`）与 n8n v2 执行序

Kahn 拓扑排序 + `ready.sort()` 确定性（与 coze `compiler/workflow.py:28-58` 逐行
同构）。与 n8n v2 的关系：

- **安全**：任一 Kahn 线性化都保证每个节点的直接上游先于它；对 n8n"多输入等齐后执行"
  （runtime-chain §3.6）的运行时，按此序调度不会早于依赖。
- **不完全等价**：n8n v2 实际出队序 = `addNodeToBeExecuted` 按**连接迭代序** FIFO
  push（runtime-chain §3.5-3.6）；Kahn 排序的 ready 集合排序是编译器自定的确定性
  线性化，**独立分支间的先后顺序与 n8n 运行时轨迹不必一致**（对无副作用的纯数据流
  不可观察；对 IO 节点可观察为时序差异）。IR 消费方必须按"输入就绪即等齐"执行，
  不能把 execution_order 当严格同步序。`settings.executionOrder:'v1'`（position 序，
  legacy）未建模（alignment 建议 #3）——IR 应携带 executionOrder 语义标记（P2-5）。
- **Cycle 双保险**：checker 三色 DFS（`validator.py:121-158`）+ Kahn 未闭合 raise
  （`compiler/workflow.py:56-60`）；矩阵 10 个故意环全部正确检出且不进 PASS。

### 3.5 合成 Exit 的两个边界

- terminals 判定（`parser/workflow.py:162-175`）：main 入 ∧ ¬main 出 ∧ ¬sink ∧
  ¬ai_referenced —— 正确（respondToWebhook 不接 exit；AI 子节点经 ai_* 连接不参与
  main 拓扑）。Exit 无出边，不会引入伪环。
- **P1 边界缺口**：终端多输出节点（Switch）的 exit 边按 `REGISTRY` 硬编码
  `output_ports=4`（`node_type.py:74`）收口。Switch 路由数运行时才确定（n8n 支持
  ≥2 任意路由），**路由 >4 的终端 Switch 其 main_4+ 端口无 exit 汇** → 该路由数据在
  IR 中没有 sink。143 矩阵未命中（fixture 路由 ≤4），真实场景可达。`_output_port_names`
  的 `used_ports` 分支（`parser/workflow.py:63-75`）在 exit 合成路径**实际不可达**
  （terminal 无出边 → connected_ports 恒空），是死分支。**改法**：对 switch 从
  `parameters.conditions` 推导路由数（静态已知时），或 exit 收口改为按
  "连接引用的最大端口 + 参数声明路由"取上界。

---

## 四、序列化与兼容

### 4.1 白名单校验

`typed_ir.py` 逐层 allowed 集（顶层 `:22-26`、workflow/nodes/connections/manifest/
error_policy/dependencies 全覆盖）+ 严格类型装载 + digest。`_validate_fields`
（`:292-302`）未知字段即拒。严谨度与 coze（`coze typed_ir.py` 553 行）同级。
**浅处**：`config.js` 只查 effect/runtime（见 2.4，P2-7）；`js_ast` 仅要求 dict
（`:172-176`）。

### 4.2 digest

`compute_typed_ir_digest`（`:54-65`）：除 digest 外全字段，`sort_keys + compact
separators + allow_nan=False` → 确定性、与 pretty-print 无关（to_json 与 digest
canonical 形式解耦，`compiler/workflow.py:146-148`）。边界：Python `json.loads`
默认接受 NaN/Infinity 字面量，参数含 NaN 时 digest 的 `allow_nan=False` 会 raise
ValueError → 显式失败（可接受；P2-13 可前置严格模式）。

### 4.3 to_index 向后兼容

双端兜底：IR 校验可选（`typed_ir.py:250-254`）+ `Connection.from_dict` 默认 0
（`connection.py:38-41`）；回归固化（`test_alignment_bounds.py:168-177`）。
**已闭环**。

### 4.4 IR_VERSION 演进策略

`typed_ir.py:19,95-96`：`format_version` **精确 == 1**，无 accepted 范围、无迁移
路径（coze 同样精确匹配 v3）。对 v1→v2：建议引入 `IR_VERSION_ACCEPTED` 集合 +
version bump 时保留旧 loader 分支（P2-6）。digest 校验在升级时也会一并失效（因为
canonical 内容变了）——这其实是特性（篡改即失败），文档化即可。

---

## 五、测试与治理

### 5.1 分层与基线（全部本次复跑验证）

| 层 | 载体 | 实测 |
|---|---|---|
| unit | test_parser/expression/checker/type_roundtrip/misc/alignment_bounds/compiler/cli | 通过 |
| integration(node) | test_code_js（acorn 桥 + 坏代码 `1aaa;` 3:26 精确抓出） | 通过 |
| matrix(n8n repo) | test_batch_matrix（143 工作流全链路 + 集合级断言） | 130/10/3 复现 |
| coverage | tests/coverage.py（trace + ast 可执行行） | 93.5% (1992/2131) |

`tests/helpers.py` 集中 fixture + `n8n_repo()` + `require_n8n_repo` skip 守卫；
TESTING.md 治理规则（禁 sys.path hack、命名规范、回归先行、提交前全量）纪律良好。
覆盖率工具特性如实文档化（trace 只记"至少执行一次"、多行 import 续行致 >100% 假象、
装饰器偏移）——**可信且防误读**。

### 5.2 覆盖缺口（测试层级的真实盲区）

1. `load_typed_node` 的 input_sources round-trip **无测试**（P1 缺口无回归网）。
2. `type_mismatch`/`source_field_missing` 只有手造 AST 测试，**无真实触发路径测试**。
3. acorn **版本**对拍有证据（8.14.0 == n8n），但**解析选项**（ecmaVersion/sourceType/
   allowReturnOutsideFunction）与 n8n tournament 实际选项**无对拍测试**（P2-12）。
4. exit 合成对 Switch 路由数无边界测试（P1 缺口无回归网）。

---

## 六、问题清单（按优先级）

### P0（正确性）

**未发现已证实 P0。** 130 PASS 工作流的 IR 产物、digest、拓扑序经实测一致；
所有外部 gate 失败路径均为显式 `JSInfraError`/`ValueError`，无静默降级。
以下两个最接近正确性风险的项按触发条件归入 P1。

### P1（结构）

1. **`load_typed_node` 丢弃 input_sources/output_sources**
   `ast_nodes/mappings.py:99-101`（coze 对照 `ast_nodes/nodes.py:271-272`）。
   IR→AST round-trip 丢失全部依赖/引用信息；未来 runtime adapter 直接踩坑。
   改法：补 `FieldInfo.from_dict` 回填 + round-trip 测试。**中改动 / 高影响**。
2. **Switch 输出端口数硬编码 4 → 终端 Switch 路由>4 时 IR 缺 sink**
   `ast_nodes/node_type.py:74` + `parser/workflow.py:63-75`（used_ports 死分支）。
   改法：从 `parameters.conditions` 推导路由数取上界收口。**小改动 / 中影响**。
3. **Python 模式拒绝复用了 `JSInfraError`（基础设施错误类型）**
   `parser/node_adaptors.py:26-31`。把"源不受支持"与"acorn 桥不可用"混为同类；
   下游 catch JSInfraError 判断"Node 缺失"会得到假信号。仍显式失败（gate 要求满足），
   但类型语义错。改法：拆 `UnsupportedSourceError`（同样显式，exit 可区分）。
   **小改动 / 中影响**。
4. **Code 依赖 to_path "items" 前缀约定 vs 表达式绑定参数路径约定并存**
   `parser/workflow.py:253-262`（Code）vs `:210-236`（表达式）。同一 IR 里两种路径
   语义，消费方需按节点类型分支。改法：统一为 item.json 相对路径（去掉 items 前缀）
   或 IR schema 文档显式声明该约定。**小改动 / 中影响**（注意改 digest 语义，
   需与下游同步）。

### P2（建议）

1. type_mismatch/source_field_missing 潜伏 —— 文档明说"类型检查当前近乎装饰"或补
   节点参数 schema。`checker/validator.py:162-268`。
2. REGISTRY kind 与子类 KIND 双源漂移风险 —— 生成式一致性测试。
   `ast_nodes/node_type.py:74-100` vs `node_decls.py`。
3. `_output_port_names` used_ports 死分支 —— 删除或让 exit 收口真正使用它。
4. IR 无 UNKNOWN 表达式标记；`_dynamic` 被丢弃（`parser/workflow.py:181`）。
5. execution_order 与 n8n v2 出队序不等价 + v1 序未建模 —— IR 携带
   `settings.executionOrder` + 文档注明"等齐执行，非同步序"。
6. IR_VERSION 精确匹配无 accepted 范围/迁移路径 —— 引入 `IR_VERSION_ACCEPTED`。
7. `config.js` 白名单校验浅（`typed_ir.py:164-176`）—— deps/output/errors/warnings
   补类型校验。
8. contract vs js_ast 双轨无"contract==derive(js_ast)"不变量测试。
9. `Value` 类死代码（`values/value.py`，无生产者）；`EntryNode` 在 parse 路径未合成
   （仅 load_typed_node 可达）。
10. `_type_at_path`（`checker/validator.py:162`）与 `NodeDecl.output_type_at`
    （`ast_nodes/nodes.py:96`）重复实现，仅后者有测试 —— 合并或删一份。
11. CLI 退出码不区分基础设施错误与源错误（`cli.py:73-75` 均 exit 2）——建议
    JSInfraError 用独立 exit 码或 stderr 前缀，便于自动化区分。
12. acorn 解析选项与 n8n tournament 对拍测试（版本已对齐 8.14.0，选项未固化）。
13. Python json.loads 接受 NaN → digest allow_nan=False 显式失败 —— 前置严格 JSON
    模式使报错更早更清晰。

---

## Trade-offs

| 方案 | 优点 | 缺点 |
|---|---|---|
| A. 保持 contract 权威 + js_ast 负载（现状） | 下游可拿到 ESTree；digest 防篡改 | 双轨漂移风险；IR 膨胀；需不变量测试 |
| B. IR 只带 contract 不带 js_ast | 更小、更稳、单事实源 | 下游失去 AST（如需源码级工具需回退 payload） |
| C. execution_order 保留（现状） | 确定性、可测试、供调度参考 | 与 n8n v2 出队序不等价，可能被误解为同步序 |
| D. execution_order 改为按 scope 的边集 + 就绪标记 | 与 n8n 等齐语义精确对齐 | 破坏 v1 IR 兼容；复杂化 |

## Consensus Addendum（ralplan 审核）

- **Antithesis（对主结论的强反驳）**：可以说"type_mismatch 潜伏 + Switch 硬编码 4 +
  load_typed_node 丢 input_sources"三个 P1 合起来说明**检查器/加载器当前是半成品
  壳**——它们占了大半 checker 代码却几乎不产生真实信号，真实价值只在图结构校验与
  Code 契约。若按"严格编译器"标准，应把 P1-1 视为 P0（任何 IR 消费者都会踩）。
- **Tradeoff tension**：把"类型检查通电"（补节点参数 schema）与"保持零依赖、矩阵
  全绿"（schema 库是重活）之间的张力不可回避；UNKNOWN 降级策略正是这种张力的
  现阶段解法——**静态精度让位于运行时保真**。
- **Synthesis**：最经济路径是——P1-1（加载器回填）立即修（它是纯补账，不引入
  新依赖）；P1-2 用参数推导路由数小修；类型矩阵通电推迟到节点描述 schema 独立成
  数据文件（JSON）时做，保持 Python 零依赖；同时把"类型检查当前信号≈0"写进文档
  防过度信任。

## References（关键证据行号）

- `parser/workflow.py:109-208` - parse_workflow：适配、to_index 读取(:139)、Exit 合成(:162-175)、表达式/Code 绑定(:210-264)
- `parser/expression.py:36-57,62-66,79-157,177-195` - ParsedRef 分类、$node 访问器纪律、$input 三形态、UNKNOWN 降级、模板串取首个引用
- `parser/node_adaptors.py:26-31` - Python 模式拒绝复用 JSInfraError（P1-3）
- `ast_nodes/node_type.py:74-75,103-107` - REGISTRY：switch=4 路由硬编码（P1-2）、GenericNode 兜底
- `ast_nodes/node_decls.py:74-115,317-331` - CodeNode（contract 权威 + js_ast 负载）、EntryNode/ExitNode
- `ast_nodes/mappings.py:76-101` - load_typed_node 不回填 input_sources（P1-1）
- `ast_nodes/nodes.py:26-28,46-107` - TYPE_CHECKING scope 引用；NodeDecl 字段；output_type_at 与 checker 重复
- `ast_nodes/connection.py:16-43` - Connection + to_index + identity
- `checker/validator.py:48-73,76-118,121-158,162-197,199-268,271-293,295-345,346-357` - 语法/连接/环/引用/可赋性矩阵/节点语义/组合
- `compiler/workflow.py:34-61,80-118,150-164` - Kahn 拓扑序、IR 序列化、compile_ast
- `compiler/dependency.py:32-118` - direct/indirect/variables/static_values 分类
- `code/js_parser.py:23-24,51-59,65-89,102-126` - JSInfraError、桥失败路径、批处理
- `code/js_ast.py:120-147,158-210,212-300` - 依赖提取/模块语法拒绝/warning 收集/形状推导/效应分类
- `code/contract.py:14-84` - CodeEffect/OutputShape/Contract/StaticContract
- `scripts/js_parse.mjs:1-44` - acorn 解析选项（latest/module/allowReturnOutsideFunction）
- `typed_ir.py:19,22-52,54-65,89-139,141-176,244-268,269-278,292-318` - IR_VERSION 精确匹配、白名单、digest、to_index 兼容
- `cli.py:26-36,39-48,73-75` - check/compile/退出码
- `manifest.py:87-148` - 资源清单构建（模型/向量库/工具/webhook/凭据）
- `tests/test_batch_matrix.py:1-64` - 143 矩阵固化（实测 130/10/3）
- `tests/test_alignment_bounds.py:119-184` - to_index 兼容/拒错固化
- `tests/test_misc_coverage.py:197-240` - JSInfraError 基础设施失败路径测试
- `tests/coverage.py:1-131` + `TESTING.md` - 覆盖率工具特性与治理基线（实测 93.5%）
- `ana-docs/n8n-runtime-chain.md:§1-§5` - n8n 运行时基准（零校验加载、= 前缀、v2 等齐、Code 沙箱）
- `ana-docs/n8n-compiler-alignment.md` - 对拍差异表（已修项 to_index/$node 纪律/import 前置；建议项 Merge 等齐下沉、require→error、$('X')、executionOrder）
- coze 对照：`/home/dev/coze_compiler/compiler/workflow.py:21-22,28-58`（编译器→manifest/typed_ir 同构、Kahn 同构）、`ast_nodes/nodes.py:257-280`（加载器回填 input_sources）


---

## 附：审核后修复记录（2026-08-18 同日实施）

审核结论落地：4 个 P1 全部处理（3 修 1 文档化），另修 2 个 P2/文档项。
验证：`python3 -m unittest discover -s tests` → **188 tests OK**；
`python3 tests/coverage.py --quiet` → **93.4%**（2005/2146）。

| 项 | 处置 | 变更 |
|---|---|---|
| P1-1 load_typed_node 丢 input_sources | ✅ 修 | `ast_nodes/mappings.py` 回填 input_sources/output_sources（对齐 coze）；回归 `test_type_roundtrip.py::test_input_output_sources_round_trip`（含旧 IR 无字段 → 空列表） |
| P1-2 Switch 路由数硬编码 4 | ✅ 修 | `parser/workflow.py` 新增 `_declared_port_count`（`parameters.rules.values`/`rules`/`options.rules` 推导）；`_output_port_names` 删除死参数 used_ports（terminal 恒空），终端 Switch 按声明路由收口 exit，>4 路由不漏收；回归 3 例（`TestMultiOutputPorts`） |
| P1-3 Python 模式误用 JSInfraError | ✅ 修 | `parser/node_adaptors.py` Python 模式改抛 `ValueError`（源不受支持，与桥不可用的基础设施错误区分）；测试同步 |
| P1-4 Code 依赖 to_path 双约定 | 📝 文档化 | `_bind_code_node` docstring 注明 "items" 前缀约定与消费方处理方式（不改 IR 形状） |
| P2-2 REGISTRY/KIND 双源漂移 | ✅ 修 | `test_type_roundtrip.py::test_registry_kind_matches_node_class` 一致性测试（当前无漂移） |
| README export 命令漂移 | ✅ 修 | README 注明 `export` 未实现（cli.py 仅 check/compile） |
| 依赖方向 | ✅ 确认 | `mappings.py → values.reference` 新增边，values 为叶子，无环（35 模块 AST 扫描复验） |

未实施（留待后续，见 P2 清单与 alignment 建议）：Merge 等齐规则下沉、`require`
warning→error、IR executionOrder 语义标记、IR_VERSION accepted 集合、acorn 选项
对拍测试固化、类型矩阵通电（需节点描述 schema 独立成 JSON 数据文件）。
