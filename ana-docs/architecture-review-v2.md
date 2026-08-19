# n8n_compiler 架构审核报告（第二轮）

审核对象：`/home/dev/n8n_compiler`（n8n 工作流 AOT 编译器前端，Python 纯标准库，~6850 行）
审核角色：编译器架构师 ×2（主会话 + architect 子代理独立视角，交叉验证）
对照基准：`/home/dev/coze_compiler` 分层规格 + `/home/dev/n8n` 运行时语义（本轮新读源码）
审核日期：2026-08-18　性质：只读，未改任何代码

> 所有关键断言均经**独立复现**：architect 报告 + 主会话复跑交叉确认；n8n 侧语义
> 有源码行级证据（`SwitchV3.node.ts` / `js-task-runner.ts` / `workflow-sdk lint`）。

## 复跑基线（本次实测，非转引）

| 项 | 结果 |
|---|---|
| `python3 -m unittest discover -s tests` | **203 tests OK**（4.0s） |
| `python3 tests/coverage.py --quiet` | **93.5% (2006/2146)** ran=203 fail=0 err=0 |
| `test_batch_matrix` | OK（130 PASS / 10 环 / 3 其他，集合级精确断言） |
| n8n 侧 | acorn 8.14.0 三方一致；Switch V3 fallback 语义、task-runner 包装、lint 选项均读到行级证据 |

## 总体结论

**结构健康，无 P0。新增 2 个 P1（正确性边界，上轮未覆盖）+ 4 个新 P2；上轮 10/13
P2 仍活。** 分层单向无环、digest 防篡改纪律、测试治理基线复跑成立；上轮 4 个 P1
修复全部落地可验证。两个新 P1 都挂在「终端多输出节点 exit 收口」与「Code 节点参数
绑定」这两条链上，均为**编译结果与 n8n 运行时语义不一致**的边界缺陷。

---

## 一、新增 P1（2 个，均实锤复现）

### P1-1｜终端 Switch V3 `fallbackOutput: 'extra'` fallback 端口漏收口（静默丢数据）

- **位置**：`parser/workflow.py:63-79`（`_declared_port_count` 只数 rules 长度）、`:189-193`（exit 收口遍历声明端口）
- **触发**：Switch typeVersion 3 + `options.fallbackOutput === 'extra'` + 终端节点。
  n8n 官方语义（`/home/dev/n8n/packages/nodes-base/nodes/Switch/V3/SwitchV3.node.ts:31-42,292,469-475`）：
  在 `rules.values.length` 索引处创建额外输出端口，未匹配项路由到该端口。
- **实锤**（主会话复现）：2 rules + fallback 终端 Switch → IR output_types 声明
  `main_0..main_3`（注册表下限兜底），但 exit 收口边仅 `main_0/main_1`，fallback
  端口 `main_2` 未匹配数据静默丢失；decompile 后 Switch **无任何出边**。
- **与上轮 P1-2（路由数>4）完全同族**，触发源从"路由数超注册表下限"变"fallback +1 端口"。
- **改法**：`_declared_port_count` 在 `fallbackOutput=='extra'` 时 `+1`；回归进
  `TestMultiOutputPorts`/`test_decompile`。工作量：小。

### P1-2｜Code 节点 `jsCode` 字面量 `{{ ... }}` 被误当 n8n 表达式（合法工作流被拒）

- **位置**：`parser/workflow.py:200-204`（对全部参数跑表达式提取，含 jsCode）；
  `parser/expression.py:23-24`（`_EMBEDDED_RE` 匹配任意 `{{...}}`）
- **触发**：jsCode 源码/注释/模板串含 `{{ $node['X'].json.foo }}` 类字面文本，
  引用的节点不存在/不可达 → `WorkflowValidationError ... depends on missing node "X"`。
- **依据**：n8n 运行时**不**对 Code 源码做表达式插值（`task-runner/src/js-task-runner/js-task-runner.ts:686`
  源码原样包进 `async function` 执行）——`{{ }}` 是纯字面量。编译器双重语义解读
  （acorn AST 一次 + 字符串正则一次）→ false reject。
- **实锤**（主会话复现）：jsCode 注释+模板串含字面量 → 编译拒绝；无 `.json` 的
  变体恰因访问器白名单落 UNKNOWN 而漏网——部分路径偶然放行，更显缺陷。
- **改法**：绑定循环对 CodeNode 跳过 `jsCode`/`code` 键（Code 依赖已由 acorn 静态
  deps 通道独立处理，`parser/workflow.py:259-285`）。**豁免须限定键名**（未来若有
  参数确实需插值则不受影响）。工作量：小。

---

## 二、P2 问题

### 上轮遗留、本轮复核仍活（10 项）

| # | 项 | 位置 | 复核状态 |
|---|---|---|---|
| P2-1 | `execution_order` 名不符实（Kahn 拓扑序 ≠ n8n 执行序） | `compiler/workflow.py:34-60` | 仍活；建议改称 `topological_order` 或文档降级 |
| P2-2 | IR_VERSION 无 accepted 集合（精确匹配即拒，v1.x 演进无通道） | `typed_ir.py:95-96` | 仍活 |
| P2-3 | config.js 白名单浅 + **contract.deps 写死不读**（序列化/反序列化不对称，IR deps 加载后变 `[]`） | `typed_ir.py:80-88`；`ast_nodes/mappings.py:111-135` | 仍活，**恶化**：从"浅"深化为"读写不对称" |
| P2-4 | contract vs js_ast 无一致不变量（可带矛盾两者过 digest） | — | 仍活 |
| P2-5 | `values/value.py` Value 死代码 | `values/value.py:14-21` | 仍活（全仓仅自引用） |
| P2-6 | `_type_at_path` 与 `output_type_at` 重复实现 | `checker/validator.py:162` vs `ast_nodes/nodes.py:58-73` | 仍活 |
| P2-7 | CLI 退出码不区分输入畸形/基础设施故障/校验失败 | `cli.py:71-75` | 仍活 |
| P2-8 | acorn 选项未固化对拍，**本轮深化**：n8n lint 是 script→module 回退（sloppy 允许 with 等），编译器 module-only strict + allowAwaitOutsideFunction → sloppy-only 语法误拒 | `scripts/js_parse.mjs:21-27`；`workflow-sdk/src/lint/code-node/js.ts:37-47` | 仍活，有 n8n 行级证据 |
| P2-9 | json.loads NaN 报错混淆（编译期报"JSON 序列化"而非"输入含 NaN"） | `typed_ir.py:80`、`cli.py:16` vs `typed_ir.py:57-63` | 仍活（实锤复现） |
| P2-10 | 类型矩阵近乎装饰（注册表节点 input/output_types 恒 any/空 props → type_mismatch 对真实工作流不可达） | `checker/validator.py:166-194` | 仍活；维持"等节点描述 schema 数据化"决策 |

### 本轮新增（4 项）

| # | 项 | 位置 | 触发/影响 |
|---|---|---|---|
| P2-11 | `__exit__` 保留名冲突，诊断误导（覆盖后报自环/静默丢节点） | `parser/workflow.py:187-188` | 用户节点叫 `__exit__`：终端→假自环报错；无连接→静默丢弃。建议合成前显式报错 + 文档标注保留名 |
| P2-12 | decompile 对缺省 `to_index` 抛 KeyError（非 ValueError，破显式失败纪律） | `runtime/decompile.py:92` vs `typed_ir.py:44-46`（to_index 可选） | validator 放行的 IR（to_index 缺省）→ `KeyError`。改 `conn.get("to_index", 0)` 与 Connection.from_dict 一致 |
| P2-13 | **error_policy 入 IR 不回写；credentials 从未入 IR**（双缺口，部署 adapter 前置条件） | `runtime/decompile.py:54-64`；`ast_nodes/node_decls.py:75-84` | 反编译产物丢 onError/retry 策略与凭据引用。**主会话独立发现同一项**（交叉印证）。凭据建议仅存 id 引用、敏感字段不入 IR |
| P2-14 | decompile 只剔 exit 收口边，不剔合成节点出边（悬空连接） | `runtime/decompile.py:81-93` | IR 含 synthetic.entry 出边时产物悬空（编译器当前不产，防御性缺口） |

另确认近死代码（coze 对齐保留）：`configs.py:14-31` ExceptionConfig、`connection.py:35-43` Connection.from_dict（默认 `from_port="0"` 非法端口名）。

---

## 三、与上轮 delta

**落地复核 ✅**：4 个 P1（input_sources 回填 / Switch 路由数推导 / Python 模式 ValueError /
to_path 文档化）+ REGISTRY/KIND 一致性测试——全部在代码与测试中确认。

**仍活 10/13**：P2-1..P2-10 见上表。恶化 2 项：P2-3（白名单浅 → 读写不对称）、
P2-8（版本一致 → 选项策略不一致，拿到 n8n 行级证据）。

**新发现**：P1×2 + P2×4（保留名 / to_index KeyError / error_policy+credentials 还原 /
合成节点出边悬空）。

## 四、框架逐项结论（要点）

1. **分层**：无环。runtime/decompile → typed_ir + ast_nodes 与 compiler→顶层 同构
   （规格继承）。IR 的真正消费方（执行器/deploy adapter）尚不存在，运行时语义承诺
   仅 A2 最小探针覆盖。
2. **边界**：模型自洽；两处不对称——Connection.from_dict 默认非法端口名、contract.deps
   读写不对称（P2-3）。
3. **IR 设计**：白名单+digest 纪律优秀；缺口集中在版本演进（P2-2）、语义字段名（P2-1）、
   config.js 校验（P2-3/P2-4）。
4. **表达性**：UNKNOWN 降级纪律良好；exit 收口依赖 `_declared_port_count` → 两个 P1
   都挂在这条链上。
5. **runtime 新层**：与 parser 对称性成立（round-trip 等价 + A2 真实执行）；错误纪律
   两处破（P2-12 KeyError、P2-14 悬空边）。
6. **测试治理**：203/93.5% 可信；矩阵集合级断言防漂移强；**盲区恰好覆盖两个 P1**
   （fallback、jsCode 字面量）——测试夹具缺这两个场景。
7. **扩展性**：REGISTRY 数据友好，ncoda→n8n target profile 落数据文件即可；IR v2
   最大障碍是精确版本匹配；部署 adapter 前置 = P2-13。

## 五、优先建议排序

1. **P1-1 Switch fallback 收口**（小改，防静默丢数据，与上轮已修同族）
2. **P1-2 jsCode 误提取豁免**（小改，消除 false-reject；豁免限定 `jsCode`/`code` 键）
3. **P2-3 contract.deps 读写对称**（小改，消除只写不读漂移源）
4. **P2-13 error_policy 回写 + credentials 入 IR**（中改，部署 adapter 前置）
5. **P2-8 acorn script→module 回退对齐 + 选项固化测试**（小改，测试级）
6. 其余 P2 滚动处理；**类型矩阵通电维持"等节点 schema 数据化"决策，勿提前投入**

## 六、诚实标注

- **验证过**：全部行号引用、复跑输出、两个 P1 复现实锤、n8n 侧三处源码语义（亲读）。
- **强推断**：n8n 对 Code 源码不做插值（task-runner 原样包装 + 表达式只作用于参数）；
  类型矩阵不可达（逐类读声明 + `_shape_unknown` 语义）。
- **中等推断**：`__exit__`/`__entry__` 用户命名冲突真实发生概率；sloppy-only 语法在
  Code 节点中的实际出现率（矩阵 143 工作流无一命中）。

---

## 附：审核后修复记录（2026-08-18 同日实施）

审核结论落地：2 个新 P1 全部修复（按测试纪律：先补回归验证红，再修转绿）。
验证：`python3 -m unittest discover -s tests` → **205 tests OK**；
`python3 tests/coverage.py --quiet` → **93.6%**（2016/2154）。

| 项 | 处置 | 变更 |
|---|---|---|
| P1-1 Switch fallback 'extra' 漏收口 | ✅ 修 | `parser/workflow.py::_declared_port_count`：`options.fallbackOutput=='extra'` 时路由数 +1（对齐 SwitchV3 语义）；回归 `test_parser.py::test_terminal_switch_fallback_extra_ports_capped`（终端 Switch 2 rules + fallback → exit 收口 main_0/1/2） |
| P1-2 jsCode 字面量误提取 | ✅ 修 | `parser/workflow.py` 绑定循环：CodeNode 跳过 `jsCode`/`code` 键（源码是字面量，依赖由 acorn 通道处理）；回归 `test_parser.py::test_js_code_literal_braces_not_bound_as_expression`（`{{ $node["X"]... }}` 字面量不再产生虚假引用） |

未实施（P2 滚动，见清单）：P2-3 contract.deps 读写对称、P2-13 error_policy/credentials 还原
（部署 adapter 前置）、P2-8 acorn script→module 对齐、P2-12 decompile to_index 宽容（小改，
顺手下轮做）。

---

## 附二：P2 修复记录（2026-08-18 同日第二批）

验证：`python3 -m unittest discover -s tests` → **209 tests OK**；
`python3 tests/coverage.py --quiet` → **93.6%**（2021/2159）。

| 项 | 处置 | 变更 |
|---|---|---|
| P2-12 decompile 缺 to_index KeyError | ✅ 修 | `runtime/decompile.py` `conn.get("to_index", 0)`（与 Connection.from_dict 默认一致）；回归 `test_missing_to_index_defaults_to_zero` |
| P2-3 contract.deps 读写对称 | ✅ 修 | `ast_nodes/mappings.py::_contract_from_dict` 读回 `c.deps`（FieldDep 反序列化）；回归 `test_code_contract_deps_round_trip` |
| P2-13 credentials 入 IR + 反编译还原 | ✅ 修 | `ast_nodes/nodes.py::to_config_dict` 加 `credentials`（仅 id/name 引用，无敏感值）；`typed_ir._CONFIG_FIELDS` 加白名单；`runtime/decompile.py::_node_to_n8n` 非空回写；回归 `test_credentials_restored` |
| P2-13 error_policy 回写 | ✅ 修 | `runtime/decompile.py::_restore_error_policy`：非默认值（on_error≠stopWorkflow 或 retry_on_fail）回写 n8n 顶层 onError/retryOnFail/maxTries/waitBetweenTries；回归 `test_error_policy_restored` |

### P2-8 acorn 选项策略——决策记录（不立即改）

n8n lint 是 `script(sloppy) → module` 回退（允许 `with` 等 sloppy-only 语法），
编译器是 module-only strict + allowAwaitOutsideFunction。对齐会放行更多语法，
与「严格编译器 + 前置静态校验」定位冲突（用户明确要求不降低质量）。**决策**：
维持 module-only strict；偏差在文档标注；若未来真实用户场景踩 sloppy-only
语法，再增加显式 `--relaxed` 模式（不改默认）。矩阵 143 工作流无 sloppy-only
命中，当前无实际影响。

未实施（P2 剩余）：P2-1 execution_order 语义降级、P2-2 IR_VERSION accepted、
P2-4 contract/js_ast 不变量、P2-5 Value 死代码删除、P2-6 重复实现合并、
P2-7 CLI 退出码细分、P2-9 NaN 前置报错、P2-11 __exit__ 保留名、P2-14 合成
节点出边剔除。

---

## 附三：P2 清理记录（2026-08-18 第三批，剩余 P2 全部处理）

验证：`python3 -m unittest discover -s tests` → **217 tests OK**；
`python3 tests/coverage.py --quiet` → **93.6%**（2017/2154）。

| 项 | 处置 | 变更 |
|---|---|---|
| P2-1 execution_order 语义 | 📝 文档降级 | `typed_ir.py` 模块 docstring 明示：字段值是**拓扑序非 n8n 执行序**（数据驱动调度），消费方不得当执行序用；字段名不改（IR v1 兼容） |
| P2-2 IR_VERSION accepted | ✅ 修 | `_ACCEPTED_VERSIONS = {1}` + 错误信息列出支持版本（v1.x 兼容演进通道） |
| P2-4 contract/js_ast 不变量 | ✅ 修 | 新 `test_code_js.py::TestContractJsAstInvariant`：IR config.js 必须能从 js_ast 重新推导一致（多形状样例，防漂移） |
| P2-5 Value 死代码 | ✅ 删 | `values/value.py` 删除 + `values/__init__.py` 清理（全仓无生产/消费者） |
| P2-6 _type_at_path 重复 | ✅ 合 | `NodeDecl.input_type_at` 对称方法；validator 两处调用换方法，删 `_type_at_path`；回归 `test_input_type_at_path` |
| P2-7 CLI 退出码 | ✅ 修 | 约定 0 成功 / 1 校验失败（WorkflowValidationError）/ 2 输入错误 / 3 基础设施（JSInfraError）；回归 `TestCliExitCodes` |
| P2-9 NaN 前置报错 | ✅ 修 | `_read_json`/`load_typed_ir_json` 用 `parse_constant=_reject_non_finite`（输入侧拒绝，不再编译后期炸）；回归 `test_nan_input_rejected_exit_2` |
| P2-11 __exit__ 保留名 | ✅ 修 | 合成前显式检查占用 → `ValueError`（可操作信息）；回归 `test_exit_reserved_name_rejected` |
| P2-14 合成节点出边 | ✅ 修 | decompile 剔除 `from_node` 为合成节点的边（防悬空连接）；回归 `test_synthetic_entry_out_edge_dropped` |

剩余 P2 清零。下一优先级：部署 adapter（反编译 + credentials/error_policy 还原已就绪）、
ncoda → n8n target profile（REGISTRY 数据化）。
