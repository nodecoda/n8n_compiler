# n8n 运行时 ↔ n8n_compiler 对拍：差异清单与改进记录

基准文档：[n8n-runtime-chain.md](./n8n-runtime-chain.md)。本文列出编译器
与 n8n 运行时行为的逐项对拍结论：已一致的、本次已修的、以及留作后续的。
评级：✅ 一致 / 🔧 已改进 / ⚠️ 已知差异（有意保留或待办）。

## 对拍总表

| # | n8n 运行时行为 | 编译器现状 | 评级 |
|---|---|---|---|
| 1 | 未知节点 type 加载时跳过（零校验） | `node_class_for` → GenericNode 白名单 | ✅ |
| 2 | IF true=端口0 / false=端口1 | `_port_name_for` → main_0/main_1 | ✅ |
| 3 | Switch 输出端口数 = 路由数（运行时确定） | 端口名形状放行（main_N），不查存在性 | ✅ |
| 4 | 默认执行序 v2（依赖等齐，FIFO） | `compiler/workflow.py` 拓扑排序 | ✅ |
| 5 | 边 `index` = 目标输入端口索引 | **原丢弃 → 已加 `Connection.to_index`** | 🔧 |
| 6 | Merge 多输入按输入端口等齐 | to_index 已建模；等齐规则未下沉（建议项） | 🔧/⚠️ |
| 7 | `=` 前缀是表达式唯一识别符 | `is_expression` 只认完整 `={{ }}` 整串；`=abc` 视为字面量 | ⚠️ 有意（安全侧） |
| 8 | 加载时按节点描述补默认参数 | 保留原始 parameters（无节点描述库） | ⚠️ 有意 |
| 9 | `import`/`export`/动态 import 运行时错误 | js_static 前置为编译错误（更严） | ✅ 增强 |
| 10 | Code 顶层 return/await 合法 | acorn 桥 script 模式验证通过 | ✅ |
| 11 | `require` 默认全禁（vm2 resolver） | `collect_warnings` 记为 warning | ⚠️ 可升级为 error |
| 12 | `$node.X` 点形式 / `$node["X"]` / `$('X')` | 点+括号已支持；`$('X')` 标 UNKNOWN；**非 json/output 访问器不误绑** | 🔧 |
| 13 | `$node.X.param` / `.binary` / 下标访问 | 标 UNKNOWN（非 json 形状路径，绑定会误报 source_field_missing） | 🔧 |
| 14 | 表达式是任意 JS（算术/三元/函数） | 复杂表达式标 UNKNOWN 保留原串（v1 策略） | ✅ |
| 15 | 孤立节点 / 未连全端口零校验 | checker 不报 node_not_connected | ✅ |
| 16 | pinData 参与执行（manual/evaluation 模式） | parser 读 pinData → AST.pin_data | ✅ |

## 本次已实施的改进

### $node 数据访问器纪律（防误绑）

n8n 里 `$node["X"].json/.binary` 是数据访问器、`.params/.isExecuted` 是节点
访问器（`node-reference-parser-utils.ts:52-54`）；其他属性（如 `.body`）不是
数据路径，运行时求值失败。原 `_parse_node_ref` 把 `$node["X"].body.id` 绑定成
数据路径 `(body,id)`——比 UNKNOWN 更糟（checker 会把它当字段引用校验）。

修复（`parser/expression.py`）：`$node["X"]` 后只认 `json|output` 访问器，
其余返回 None → 标 UNKNOWN 保留原串。测试：
`test_expression.py::TestNodeAccessorDiscipline`（3 用例：非数据访问器 → UNKNOWN、
`.json` 无后缀 → 整对象、点形式 `.param` → UNKNOWN）。

### Connection.to_index（目标输入端口索引）

n8n 边形状 `{node, type, index}`，`index` = 目标输入端口索引
（`workflow-execute.ts:519` 用 `main[connectionData.index]` 写槽；Merge 多
输入按它等齐，`addNodeToBeExecuted:430`）。原编译器 parser 丢弃了该字段。

变更：

- `ast_nodes/connection.py`：新增 `to_index: int = 0`；`identity` 含 to_index
  （同源连同一目标不同输入端口不再误判 duplicate）；to_dict/from_dict 携带。
- `parser/workflow.py`：读取 `edge.get("index", 0)`。
- `compiler/workflow.py` + `typed_ir.py`：IR 序列化 `to_index` 字段 + 白名单
  校验（int）。向后兼容：旧 IR 无该字段 → from_dict 默认 0。
- 测试：Merge 双输入索引保留、identity 区分、IR round-trip（`test_parser.py`
  `TestMergeMultiInputIndex`、`test_type_roundtrip.py`、`test_compiler.py`）。

## 后续建议（按价值排序）

1. **Checker 下沉 Merge 等齐语义**：多输入目标节点（
   `@n8n/nodes-base.merge`）的入边应覆盖全部输入端口（main[0..n-1]），否则
   运行时永远等不齐。规则可做成 warning（n8n 编辑器容忍部分连接）。
2. **`require` warning → error**：默认配置（builtin 白名单空、external 关闭）
   下 `require` 运行必失败。可依 `NODE_FUNCTION_ALLOW_BUILTIN` 配置判断，但
   编译器不应读环境变量 → 建议直接升 error（与 import/export 同档），文档注明
   "配置白名单后可放行"。
3. **executionOrder 记录进 IR**：`settings.executionOrder`（v1=position 排序 /
   v2=依赖等齐）影响执行序语义，IR workflow meta 可携带；checker 拓扑序按 v2
   校验即可（v1 是 legacy）。
4. **`$('X')` 引用识别**：n8n 支持 `$('NodeName').item.json`（node-reference
   重命名工具维护）。加入 expression parser 的 NODE 分类，误判风险低。
5. **参数默认值填充**：需要节点描述库（typeDescription JSON），v1 不做；
   影响面：缺失参数在运行时被默认值填充，checker 目前不查参数存在性，无冲突。
6. **typeVersion 匹配规则**：`getByNameAndVersion` 支持 version 数组/范围，
   我们只保留原始 type_version。影响面小（节点类不随版本切换），记录即可。

## 验证

`python3 -m unittest discover -s tests`（160 tests）+
`python3 tests/coverage.py --quiet`（93.4%）。矩阵基线（143 个 n8n 工作流）
在 test_batch_matrix.py 固化，to_index 改动后无回归。
