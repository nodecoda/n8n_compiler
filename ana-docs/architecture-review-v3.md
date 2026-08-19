# n8n_compiler 架构审核报告（第三轮）

审核对象：`/home/dev/n8n_compiler`（n8n 工作流 AOT 编译器前端，Python 纯标准库，
源码约 4,700 行：核心链 ~3,500 + typed_ir/manifest/cli ~590 + runtime ~230 + scripts ~380）
对照基准：`/home/dev/coze_compiler` 分层规格 + `/home/dev/n8n` 运行时源码（本轮独立重读）
审核角色：编译器架构师（只读，未改任何代码；仅写本报告）
审核日期：2026-08-19　性质：第三轮完整架构审核（重点：runtime 层 + A2 矩阵 5 条节点形状契约）

> 纪律：本报告所有基线数字均为**本轮独立复跑**；所有关键断言附 file:line 证据；
> v2 修复项逐条复现验证（非转引 v2 结论）；新增 runtime 层与 A2 契约按
> n8n 源码行级证据独立核对。

---

## 一、复跑基线（本轮实测）

| 项 | 结果 | 说明 |
|---|---|---|
| `python3 -m unittest discover -s tests` | **234 tests OK**（4.75s） | 独立复跑，非转引 |
| `python3 tests/coverage.py --quiet` | **COVERAGE 93.6% (2017/2154)** ran=234 fail=0 err=0 skip=0 | 同上 |
| `test_batch_matrix`（143 工作流矩阵） | OK（**130 PASS / 10 CYCLIC / 3 OTHER**，集合级精确断言） | 测试内重扫 n8n 仓库，实跑通过 |
| `test_execute_matrix`（A2 矩阵本地可测部分） | OK（结构不变量 + assert 解析判定） | 远端 7/7 见「诚实标注」 |
| acorn 版本对拍 | `n8n_compiler` = **8.14.0**（package.json:7）；`@n8n/task-runner` = 8.14.0（package.json:46）；`@n8n/workflow-sdk` = 8.14.0（package.json:108） | 三方一致，确认 |
| v2 回归集（57 例定向复跑） | **OK**（fallback 端口 / jsCode 字面量 / deps round-trip / 合成边剔除 / NaN / 注册表一致性） | 见下 |

关键修复的**独立复现**（本轮重做，非转引）：
- 终端 Switch fallback `'extra'`：2 rules + fallback → IR exit 收口边 = `main_0/main_1/main_2`（正确，v2 P1-1 已修）
- jsCode 含字面量 `{{ $node["X"].json.foo }}`（注释 + 模板串）→ Code 节点 `input_sources` 计数 = 0（正确，v2 P1-2 已修）
- A2 矩阵本地构建：7 场景产物全部生成，均含 Manual Trigger、id 正确、SW fallback 边 `main[2]→OutF`（与 n8n fallback 端口 = rules.length 语义一致）

---

## 二、总体结论

**结构健康：无 P0。新增 1 个 P1（与 v1/v2 已修缺陷同族的表达式模式 Switch 端口收口缺口）、
3 个 P2（测试治理类）。v2 全部 2 个 P1 + 12 个 P2 修复逐一验证落地。** 分层单向无环
（38/38 模块独立 DFS 验证）、digest 防篡改纪律、IR 白名单装载、外部 gate 显式失败、
round-trip 等价断言 —— 这些基线在本轮全部复跑成立。

本轮审核重心（runtime 层 + A2 契约）结论：
- **runtime/decompile.py**：忠实性良好（合成节点剔除、端口映射、id 还原、错误策略回写
  全部有测试固化且与 n8n 形状一致）。
- **runtime/deploy.py**：digest 入口强制、外部失败显式化 ✅；**REST 验证缺口已于
  2026-08-19 闭环**：7 矩阵场景经 deploy_to_n8n 部署到真实实例（nodecoda-production，
  n8nio/n8n:latest）并 execute 断言 **7/7 PASS**（与 CLI import 路径结果一致）。
  真实实例抓出 3 处契约差异并已修复（settings 必填 / id readOnly / task broker 端口
  冲突），详见「修复记录」A2-REST 行。
- **A2 矩阵 5 条节点形状契约**：4/5 被编译器正确表达且矩阵真实验证；**第 5 条
  （Set 默认替换语义）矩阵断言不充分**（详见 P2-4）。

分级汇总：**P0=0，P1=1（新），P2=3（新），P3=8（新）**；v2 遗留全部关闭（P2-8 为
文档化决策，仍活但有意）。

---

## 三、与 v2 的 Delta

### 3.1 v2 修复项：全部验证落地 ✅

| v2 项 | 状态 | 本轮证据 |
|---|---|---|
| P1-1 Switch fallback `'extra'` 端口 +1 | ✅ 已修 | `parser/workflow.py:83-89`（`fallbackOutput=='extra'` 时 count+1）；独立复现 exit 收口 main_0/1/2 |
| P1-2 jsCode 字面量 `{{}}` 豁免 | ✅ 已修 | `parser/workflow.py:218-225`（CodeNode 跳过 `jsCode`/`code` 键）；独立复现 0 条虚假引用 |
| P2-1 execution_order 文档降级 | ✅ 已修 | `typed_ir.py:9-16` 模块 docstring 明示「拓扑序非执行序」 |
| P2-2 IR_VERSION accepted 集合 | ✅ 已修 | `typed_ir.py:26` `_ACCEPTED_VERSIONS = frozenset({1})` |
| P2-3 contract.deps 读写对称 | ✅ 已修 | `ast_nodes/mappings.py:122-128` `_contract_from_dict` 读回 `c.deps`（FieldDep）；`test_code_contract_deps_round_trip` 通过 |
| P2-4 contract/js_ast 一致不变量 | ✅ 已修 | `tests/test_code_js.py:218-259` `TestContractJsAstInvariant` 通过 |
| P2-5 Value 死代码 | ✅ 已修 | `values/value.py` 已删除（`values/__init__.py` 仅 reference/variable） |
| P2-6 `_type_at_path` 重复 | ✅ 已修 | `ast_nodes/nodes.py:58-69` `input_type_at` 对称方法；validator 复用 |
| P2-7 CLI 退出码 0/1/2/3 | ✅ 已修 | `cli.py:96-100`；`TestCliExitCodes` 通过 |
| P2-8 acorn script→module 回退 | ⚠️ 文档化决策（仍活但有意） | 本轮独立核实 n8n lint 确为 script 优先、module 回退（`/home/dev/n8n/packages/@n8n/workflow-sdk/src/lint/code-node/js.ts:31-41`）；编译器保持 module-only strict。**偏差真实存在，决策记录成立**（矩阵 143 无 sloppy-only 命中；`with` 等语法被更严拒绝是有意为之） |
| P2-9 NaN 前置报错 | ✅ 已修 | `cli.py:17-20` + `typed_ir.py:74-77`（`parse_constant=_reject_non_finite`）；`test_nan_input_rejected_exit_2` 通过 |
| P2-11 `__exit__` 保留名 | ✅ 已修 | `parser/workflow.py:197-203`；`test_exit_reserved_name_rejected` 通过 |
| P2-12 decompile to_index 缺省 | ✅ 已修 | `runtime/decompile.py:120` `conn.get("to_index", 0)`；`test_missing_to_index_defaults_to_zero` 通过 |
| P2-13 credentials + error_policy 还原 | ✅ 已修 | `ast_nodes/nodes.py:92-103`（config 带 credentials）；`typed_ir.py:66` 白名单含 `credentials`；`runtime/decompile.py:54-66` 非默认值回写；`test_credentials_restored`/`test_error_policy_restored` 通过 |
| P2-14 合成节点出边剔除 | ✅ 已修 | `runtime/decompile.py:100-114`（to_node==exit **或** from_node 为合成节点）；`test_synthetic_entry_out_edge_dropped` 通过 |

### 3.2 仍活项（有意保留，非缺陷）

1. **P2-8 acorn 严格度偏差**（见上表）——文档化决策，维持 module-only strict。
2. **checker 类型矩阵信号≈0**（v1 遗留建议）——`_is_assignable`
   （`checker/validator.py:162-180`）宽松放行（any→任意、number→string 等），与 n8n
   运行时宽松转换一致，无假杀；真实价值在字段存在性/可达性校验。类型通电仍依赖
   节点描述 schema 独立成 JSON（v1 已记录）。
3. **decompile 不还原 settings/pinData/meta**（v1 已记录）——「保证编译语义往返，
   不保证完整审计往返」。注：`settings.executionOrder`（v1=位置序）不回写意味着
   依赖 v1 执行序的工作流反编译后**静默切换为 v2 语义**，部署方需知晓（P3-8）。

### 3.3 新发现（本轮）

| 级别 | 项 | 一句话 |
|---|---|---|
| **P1** | N1：Switch v3 `mode:'expression'` 端口收口缺口 | `_declared_port_count` 不处理 expression 模式，`numberOutputs>4` 终端 Switch 的 IR exit 收口漏 main_4/5（与已修两个 P1 同族） |
| **P2** | N2：node 层缺依赖 skip 守卫 | `tests/test_code_js.py` 无 Node 时 33 个 ERROR 而非 skip，与 TESTING.md/coverage.py 声称的「按层 skip」矛盾 |
| **P2** | N3：runtime/ 与 scripts/ 不在覆盖率口径内 | `tests/coverage.py:25-29` 的 PACKAGES/TOP_MODULES 不含 runtime——最新层（deploy 等）不受 CI 门禁追踪 |
| **P2** | N4：A2 矩阵断言不充分 | Set「默认替换语义」未真实验证（Out 只读 `$json.y`，merge 结果同样通过）；场景注释过度声称 |
| P3 | N5-N12：见第四节 P3 清单 | 8 项小问题 |

---

## 四、新发现详情（含复现证据）

### P1-N1｜终端 Switch v3 `mode:'expression'` + `numberOutputs>4` 端口收口缺口

- **位置**：`parser/workflow.py:63-90`（`_declared_port_count` 只处理 `rules`/`options.rules`，
  不处理 `mode:'expression'`）；`:189-193`（exit 收口遍历声明端口）。
- **n8n 语义**：`SwitchV3.node.ts:24-29`——`mode==='expression'` 时输出端口数 =
  `parameters.numberOutputs`，与 rules 无关。
- **独立复现**（本轮）：终端 Switch `{mode:'expression', numberOutputs:6}` →
  编译后 IR exit 收口边仅 `main_0..main_3`（注册表下限 4 兜底），**main_4/main_5
  漏收**；期望 main_0..main_5。
- **性质**：与 v1 P1-2（路由数>4）和 v2 P1-1（fallback 'extra'）**完全同族**——
  终端多输出节点端口声明链第三次出现漏网分支。decompile 侧因剔除合成边而不受影响，
  但 IR 层面的「工作流终端输出」表示不完整，IR 消费方（执行/部署 adapter）会静默
  丢掉 main_4/5 的输出语义。
- **改法**：`_declared_port_count` 增加 `mode=='expression'` 分支（`numberOutputs` 为
  int 时返回该值，否则 None 回退注册表）；回归进 `TestMultiOutputPorts`
  （`tests/test_parser.py:176-281`，现有 5 个终端 Switch 用例同处）。工作量：小。

### P2-N2｜node/acorn 层「缺依赖 skip」守卫未实现（治理声称与事实不符）

- **位置**：`tests/test_code_js.py`（34 个用例，无任何 skip 守卫）；
  `tests/coverage.py:29`（注释「无 Node 时该组 skip，覆盖率会略降」）；
  `TESTING.md`（「缺依赖时按层 skip」）。
- **独立复现**（本轮）：`NODE=/nonexistent/node python3 -m unittest tests.test_code_js`
  → **Ran 34, errors=33**（JSInfraError），不是 skip。n8n-repo 层有
  `require_n8n_repo`/`skip_unless_n8n_repo`（`tests/helpers.py:44-68`），但 node 层
  **没有任何守卫**。CI 机器缺 Node 时全量测试会红，而非文档声称的 skip。
- **改法**：`tests/test_code_js.py` 加类级 `setUp` 守卫（`shutil.which("node")` 或
  `NODE` 环境变量探测，缺失 → `SkipTest`），与 helpers 的 n8n-repo 守卫同模式；
  同步修正 coverage.py 注释。工作量：小。

### P2-N3｜runtime/ 与 scripts/ 不在覆盖率口径内

- **位置**：`tests/coverage.py:25-29`——`PACKAGES = (parser/checker/compiler/code/
  ast_nodes/type_system/values/scope)`、`TOP_MODULES = (typed_ir, manifest, cli)`。
- **独立复现**（本轮）：完整覆盖率报表**无任何 runtime/ 行**；`runtime/decompile.py`
  （140 行）与 `runtime/deploy.py`（90 行）——本轮审核重点、全项目最新且风险最高的
  层——完全不受 93.6% 门禁追踪。测试存在（test_decompile 26 例 / test_deploy 10 例），
  但门禁无法发现该层回归。
- **改法**：`PACKAGES` 增加 `"runtime"`（或 TOP_MODULES 增加 `decompile`/`deploy`），
  重跑基线并更新 TESTING.md 数字。工作量：小。

### P2-N4｜A2 矩阵「Set 默认替换语义」断言不充分（注释过度声称）

- **位置**：`scripts/execute_matrix.py:105-118`（set_assignments 场景）——Out 节点
  `return { y: $json.y };`，断言 `{"y": 5}`。
- **问题**：`includeOtherFields=false` 的替换语义（未赋值字段 x 被丢弃）**无法被该
  断言区分**——若 n8n 实际执行的是 merge 语义（输出 `{x:5, y:5}`），`$json.y` 仍为 5，
  断言照常 PASS。注释声称「默认替换：仅保留赋值的 y」超出了断言实际验证的范围。
- **改法**：Out 节点改为 `return { y: $json.y, has_x: 'x' in $json };`，断言
  `{"y": 5, "has_x": false}`（真实区分替换/合并）。同理，契约 3（IF strict
  rightValue 数字字面量）目前只验证了 happy path（数字字面量可用），未验证
  反向（字符串 "3" 被拒）——该契约属生成侧（ncoda 产出数值 JSON），编译器仅透传，
  建议在文档中如实标注「仅正向验证」。工作量：小。

### P3 清单（8 项，顺手可修）

| # | 项 | 位置 |
|---|---|---|
| P3-5 | `cli.py` 跨模块导入私有符号 `typed_ir._reject_non_finite`（建议公开导出或文档化） | `cli.py:20` |
| P3-6 | 测试 fixture `set_node` 默认 `typeVersion:3` + `assignments.assignments` 形状，违反文档化「≥3.3 才用 assignments」契约（n8n `Set/v2/manual.mode.ts:189` `typeVersion<3.3` 走旧 fields.values，assignments 被静默忽略）；当前仅喂 parse/check 测试无实害，但任何未来矩阵场景复用该 fixture 会静默错执行 | `tests/helpers.py:92-100` |
| P3-7 | switch_fallback 场景注释自相矛盾（「fallback 端口是 main[2]」vs「此处连 main[1] 模拟」）；实际边 `("SW","OutF",2,0)` 是**正确**的（main[2]），注释陈旧需删 | `scripts/execute_matrix.py:216-220` |
| P3-8 | decompile 不还原 `settings.executionOrder`（v1=位置序）→ 依赖 v1 语义的工作流反编译/部署后静默变 v2；已在模块 docstring 记录，建议在 decompile-roundtrip.md 明示此边界 | `runtime/decompile.py:9-19` |
| P3-9 | `runtime/` 无 `__init__.py`（namespace package 可用，但与其余包不一致） | `runtime/` |
| P3-10 | Python 模式 Code 节点抛 `ValueError` → CLI 退出码 2（「输入错误」），把「合法但不受支持」归类为「畸形输入」；stderr 信息清晰可辨，仅分类语义不精确 | `parser/node_adaptors.py:25-32` + `cli.py:101-103` |
| P3-11 | 文档数字漂移：README.md/TESTING.md 记录「227 tests」，本轮实测 **234** | `README.md`、`TESTING.md` |
| P3-12 | `_link` 死函数（pass 占位） | `scripts/execute_matrix.py:56-58` |

---

## 五、逐框架结论

### 5.1 分层 / 依赖方向 —— ✅ 通过

- 独立 AST 扫描（38 个源码模块，含本轮新增 runtime/）：**无环**，方向
  `parser → ast_nodes/type_system/scope → checker → compiler → typed_ir`，
  `runtime → typed_ir/ast_nodes`，`deploy → decompile`，`scripts/cli` 居顶。全部单向。
- `compiler/workflow.py:13-16 → typed_ir/manifest` 的「向上」边为 coze 规格继承
  （与 coze_compiler 同构），IR 产出与装载同契约两端，IR_VERSION 演进需同步升——v1 已记录。
- `ast_nodes → code.contract`（CodeNode 一等公民的代价）依旧无环；`code/` 保持叶子。
- 唯一新观察：`cli.py:20` 直引 `typed_ir` 私有符号（P3-5），边界纪律小瑕疵。

### 5.2 合成节点模型 —— ⚠️ 一处缺口（P1-N1）

- entry 合成当前不产生（n8n 有真实 trigger），exit 收口对 **rules 数、fallback 'extra'、
  SplitInBatches（注册表 2）** 均正确；**expression 模式 Switch 漏网**（P1-N1）。
- 收口边在 decompile 时正确剔除（含合成源出边，P2-14 已修）；`__exit__` 保留名显式
  拒绝（P2-11 已修）。

### 5.3 序列化与兼容 —— ✅ 通过

- `IR_VERSION=1` + `_ACCEPTED_VERSIONS={1}`（`typed_ir.py:26`）；digest = 除 digest 外
  全语义字段的规范 JSON SHA-256（`typed_ir.py:80-87`）；白名单严格装载
  （`_validate_fields` 全量字段级校验）；NaN/Infinity 输入侧拒绝（P2-9 已修）。
- contract.deps / credentials / error_policy 读写对称（P2-3、P2-13 已验证）。
- 兼容性边界：旧 IR 缺 `to_index` → 默认 0；缺 input_sources → 空列表
  （`mappings.py:126-127`）——向后兼容良好。

### 5.4 Code 节点 JS 子系统 —— ✅ 通过（一处文档化偏差）

- acorn 8.14.0 三方一致（本轮核实）；module-only strict + `allowReturnOutsideFunction` +
  `allowAwaitOutsideFunction`（`scripts/js_parse.mjs:20-27`）。
- import/export/动态 import 前置为编译错误：与运行时语义一致——task-runner 把源码
  原样包进函数体（`/home/dev/n8n/packages/@n8n/task-runner/src/js-task-runner/js-task-runner.ts:686`
  `module.exports = async function VmCodeWrapper() {${code}\n}()`），函数体内 ESM 语句
  是语法错误，编译器更早更准地拒绝。✅
- `require` 记为 warning（运行时按 `NODE_FUNCTION_ALLOW_BUILTIN`/external 配置门控，
  task-runner 有 RequireResolver）——warning 而非 error 正确。
- P2-8 偏差（n8n lint script→module 回退 vs 编译器 module-only）**真实存在**，
  但为文档化决策（更严 = 有意）；无实际命中场景（143 矩阵零 sloppy-only）。

### 5.5 checker 类型矩阵有效性 —— ⚠️ 低信号（设计使然，已记录）

- `_is_assignable`（`checker/validator.py:162-180`）宽松（any 通吃、number→string 等
  运行时可转方向放行），与 n8n 运行时转换一致，**无假杀**；「source_field_missing」仅对
  静态可知的 object 属性集生效（`_shape_unknown` 防 ANY/空 props 误报）。
- 真实价值在引用存在性/可达性/环检测；类型维度信号≈0（v1 已记录，需节点描述 schema
  数据化后才通电）。**结论：不构成缺陷，但防过度信任**——v1 文档化建议仍有效。

### 5.6 测试治理 —— ⚠️ 两处缺口（P2-N2、P2-N3）+ 一处断言不足（P2-N4）

- 分层守卫：n8n-repo 层有 skip（`helpers.py:44-68`）；**node 层无**（P2-N2，实测 33 ERROR）。
- 覆盖率：93.6% 真实（trace 解释级 + ast 可执行行分母；>100% 模块为已文档化的
  import 续行假象，本轮确认存在且 TESTING.md 描述准确）；**runtime/scripts 在口径外**
  （P2-N3）。
- 矩阵回归：143 工作流集合级精确断言（`test_batch_matrix.py`）通过；A2 场景结构
  不变量 + assert 解析判定有测试；**Set 替换语义断言不足**（P2-N4）。

---

## 六、优先建议排序（风险 × 工作量）

| 优先级 | 项 | 工作量 | 风险 | 理由 |
|---|---|---|---|---|
| 1 | **P1-N1** Switch expression 模式端口收口 | 小（~10 行 + 2 回归） | 高 | 与两个已修 P1 同族的第三分支；静默丢 IR 终端输出语义 |
| 2 | **P2-N2** test_code_js 缺 Node skip 守卫 | 小（类级 setUp） | 中 | 治理声称与实际不符；CI 缺 Node 即全红 |
| 3 | **P2-N3** runtime/ 入覆盖率口径 | 小（PACKAGES +1 项 + 重基线） | 中 | 最新最险层（deploy）不受门禁 |
| 4 | **P2-N4** 矩阵断言强化（Set 替换语义） | 小（改 Out 节点断言） | 中 | 注释声称超出断言能力，防误导 |
| 5 | P3-5~P3-12 | 各 ~10-30 分钟 | 低 | 顺手清理（含文档数字 227→234） |

修复纪律（沿用 v2）：每项先补回归（红）再修（绿）；P1-N1 回归进 `TestMultiOutputPorts`
与 `test_decompile`，P2-N4 改 `_scenarios` 后本地 `execute_matrix.py build` 验证产物形状。

---

## 七、诚实标注（未覆盖项与置信度）

1. **远端 A2 矩阵「7/7 PASS」无法在本会话独立复跑**：远端 n8n 实例
   （nodecoda-production，腾讯云内网 docker 源）不在本会话可达范围。本地已复跑的
   是：7 场景产物结构/形状、assert 命令的日志前缀解析与 PASS/FAIL 判定
   （`tests/test_execute_matrix.py` 全覆盖）。「7/7」数字本身取自
   `ana-docs/decompile-roundtrip.md`（2026-08-19 记录）——**置信度中高**（记录新鲜且
   结构测试全过），但按纪律不冒充独立复现。
2. **runtime/deploy.py 真实实例验证已闭环（2026-08-19）**：`scripts/execute_deploy.py`
   把 deploy_to_n8n 加入远端矩阵，7 场景全部 REST 部署 + execute 断言 **7/7 PASS**
   （与 CLI import 路径产物一致、结果一致）。真实实例抓出 3 处 mock 测不到
   的契约差异并已修复（见修复记录 A2-REST 行）——mock 单测之外的真实运行证据
   不再为零。
3. **n8n 侧行号**基于 2026-08-18 快照（与 v1/v2 同源），随 n8n 演进可能偏移。
4. 覆盖率 93.6% 的口径**不含 runtime/scripts**（P2-N3），不是全仓真实值；判断缺口时
   仍以函数体模块（checker/validator 等）+ 代码审查为准。
5. 本报告未重跑 n8n 仓库构建或 e2e；全部结论基于静态源码 + 本地测试 + 上文列出的
   独立复现。

**置信度自评**：代码级断言（修复验证、P1-N1/P2-N2/P2-N3/P2-N4、分层、序列化、JS 子系统）
——**高**（均有独立复现或 file:line 证据）；远端执行类断言（7/7、deploy 实跑）——
**中**（记录/源码推断，无本会话实跑）。

---

## References（关键证据行号）

- `parser/workflow.py:63-90` - `_declared_port_count`：rules/fallback 处理正确，**缺 expression 模式分支（P1-N1）**
- `parser/workflow.py:83-89` - fallback 'extra' +1（v2 P1-1 修复，已验）
- `parser/workflow.py:218-225` - jsCode/code 键豁免（v2 P1-2 修复，已验）
- `parser/workflow.py:197-203` - `__exit__` 保留名拒绝（P2-11）
- `ast_nodes/mappings.py:122-128` - `_contract_from_dict` 读回 deps（P2-3）
- `ast_nodes/nodes.py:58-69,92-103` - `input_type_at` 合并（P2-6）；config 带 credentials（P2-13）
- `checker/validator.py:162-180,255-270` - `_is_assignable` 宽松矩阵；`_shape_unknown` 防误报
- `typed_ir.py:26,66,80-87` - accepted {1}（P2-2）；credentials 白名单；digest 规范
- `runtime/decompile.py:54-66,96-100,110` - error_policy 回写；合成边剔除（P2-14）；to_index 缺省（P2-12）
- `runtime/deploy.py:26-77` - digest 入口强制 + 显式失败（HTTP/网络/非 JSON → ValueError）
- `scripts/execute_matrix.py:105-118,216-220` - set_assignments 断言不足（P2-N4）；fallback 注释陈旧（P3-7）
- `scripts/execute_matrix.py:56-58` - `_link` 死代码（P3-12）
- `tests/coverage.py:24-29` - 口径不含 runtime/scripts（P2-N3）；node skip 注释与事实不符（P2-N2）
- `tests/test_code_js.py:1-259` - 无 Node skip 守卫（P2-N2，实测 33 ERROR）
- `tests/helpers.py:44-68,92-100` - n8n-repo skip 守卫；`set_node` fixture typeVersion 3 + assignments（P3-6）
- `tests/test_batch_matrix.py:1-231` - 143 工作流集合级精确断言（130/10/3，本轮通过）
- n8n 侧：`/home/dev/n8n/packages/nodes-base/nodes/Switch/V3/SwitchV3.node.ts:24-29,37-42` - expression 模式端口数 / fallback extra 端口
- n8n 侧：`/home/dev/n8n/packages/nodes-base/nodes/Set/v2/SetV2.node.ts:23`（version 3-3.5）、`v2/manual.mode.ts:169-176,189`（assignments ≥3.3；typeVersion<3.3 走旧路径）
- n8n 侧：`/home/dev/n8n/packages/cli/src/utils.ts:32-40` + `execute.ts:80` - findCliWorkflowStart（起始 Trigger 契约）
- n8n 侧：`/home/dev/n8n/packages/@n8n/task-runner/src/js-task-runner/js-task-runner.ts:686` - 源码原样包函数体（jsCode `{{}}` 字面量豁免依据）
- n8n 侧：`/home/dev/n8n/packages/@n8n/workflow-sdk/src/lint/code-node/js.ts:31-41` - script→module 回退（P2-8 偏差核实）
- n8n 侧：`/home/dev/n8n/packages/cli/src/workflows/workflow-entity-mapper.ts:8-22` + `public-api/v1/handlers/workflows/workflows.handler.ts:94-123` - REST 部署 DTO 兼容性

---

## 附：审核后修复记录（2026-08-19 实施）

全部按 v2 纪律（先补回归红 → 再修绿），逐项验证：

| # | 修复 | 证据 |
|---|---|---|
| **P1-N1** | `_declared_port_count` 增加 `mode:'expression'` 分支（`numberOutputs` 取端口数，int/float 整值均可，bool/缺失回退注册表） | 回归 `test_terminal_switch_expression_mode_ports_capped`（test_parser + test_decompile 各 1）先红后绿；实测 `numberOutputs:6` → exit 收口 main_0..5 |
| **P2-N2** | `tests/helpers.py::skip_unless_node` 类级守卫（复用 `code/js_parser.find_node` + 可执行性校验，与 n8n-repo 守卫同模式）；test_code_js 6 个类全部装饰 | `NODE=/nonexistent/node` → **skipped=34**（原 errors=33）；正常 → OK |
| **P2-N3** | `tests/coverage.py` PACKAGES 增加 `"runtime"`；回归测试又抓出 `_is_project_module` 的 `rel.name` vs `TOP_MODULES` 不匹配——typed_ir/manifest/cli 从未进报表（死配置） | 报表出现 runtime/decompile 91.6%、runtime/deploy 105.6%；口径修复后顶层模块真实计入（typed_ir 83.5% / manifest 90.7% / cli 91.7%）；新基线 92.7% (2544/2743) |
| **P2-N4** | set_assignments Out 改为 `return { y: $json.y, has_x: 'x' in $json };`，断言 `{"y": 5, "has_x": False}` | **真实 n8n 执行 PASS**（nodecoda-production）：替换语义实证（has_x=False），非注释声称 |
| **P3-5** | `_reject_non_finite` 公开为 `reject_non_finite`（typed_ir），cli.py 改公开导入 | 全仓无 `_reject_non_finite` 残留 |
| **P3-6** | `set_node` fixture typeVersion 3 → 3.5（与 assignments 形状契约一致） | 全量 237 tests OK |
| **P3-7** | switch_fallback 陈旧注释删除，改为准确的 main[2] 说明 | 注释与边 `("SW","OutF",2,0)` 一致 |
| **P3-8** | decompile-roundtrip.md 明示 `settings.executionOrder` 不还原边界（v1→v2 静默迁移） | 文档新增 P3-8 段 |
| **P3-9** | `runtime/__init__.py` 补齐（与其余包一致） | 文件存在 |
| **P3-10** | `UnsupportedSourceError(ValueError)` 区分「合法但不受支持」；CLI 映射退出码 1，stderr 前缀 `unsupported source:` | 回归 `test_python_mode_code_exit_1`；原 ValueError 断言兼容（子类） |
| **P3-11** | README.md/TESTING.md 基线 227→236（后为 237）tests、93.6%→93.9% | 文档已同步 |
| **P3-12** | `scripts/execute_matrix.py` `_link` 死函数删除 | 无残留引用 |
| **A2-REST** | deploy.py REST 真实实例验证缺口闭合：① envelope 补 `settings.executionOrder:'v1'`（REST schema 强制，缺失 -> 400）；② deploy 前剥 `id`（workflowCreate 标 readOnly，携带 -> 400）；③ execute 用独立 `N8N_RUNNERS_BROKER_PORT`（容器内常驻服务占 5679） | 新脚本 `scripts/execute_deploy.py`（deploy/execute/assert 三段，复用 execute_matrix 场景与断言）；真实实例 **7/7 PASS**（与 CLI import 结果一致）；回归 `test_deploy.py` +2 断言（settings 存在 / id 不存在），253 tests OK |

**复跑基线（修复后）**：`python3 -m unittest discover -s tests` → **253 tests OK**；
`python3 tests/coverage.py --quiet` → **92.7% (2544/2743)** ran=253 fail=0 err=0 skip=0。

**复跑基线（A2-REST deploy 闭环后，2026-08-19）**：`python3 -m unittest discover -s tests`
→ **253 tests OK**；`python3 tests/coverage.py --quiet` → **92.8% (2546/2745)**
ran=253 fail=0 err=0 skip=0（deploy.py `pop("id")` + decompile `settings` 新行被
回归覆盖，口径只升不降）。真实实例 REST 部署 7/7 PASS 见
`ana-docs/decompile-roundtrip.md`「REST 部署真实实例验证」。

**补回归测试（2026-08-19 二轮）**：为修复行为固化 16 个新回归——P1-N1 边界参数 4 例
（float 整值/bool/字符串/缺失 numberOutputs）、P2-N2 守卫行为 3 例（env 注入，test_helpers.py）、
P2-N4 断言区分替换/合并 1 例、P2-N3 覆盖率口径 4 例（test_coverage_tool.py，**抓出顶层模块
从未进报表的潜伏 bug**）、P3-5/P3-10 公开 API 契约 2 例。守卫测试用环境变量注入而非 mock
模块属性（装饰器闭包已绑定 find_node）。
