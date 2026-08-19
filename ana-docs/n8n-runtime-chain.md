# n8n 运行时全链条分析：文件加载 → 表达式编译 → 执行引擎 → 节点执行

分析对象：`/home/dev/n8n`（n8n 主仓库，2026-08-18 快照）。目的：为
`n8n_compiler` 的 parser / checker / IR 提供运行时行为基准（对拍）。行号
均为分析当日快照，随 n8n 演进可能偏移。

## 全链条总览

```mermaid
flowchart LR
    WF[workflow JSON / DB entity] --> WD[workflowData]
    WD --> W[new Workflow / workflow.ts]
    W --> NP[NodeTypes 解析 + getNodeParameters 补默认参数]
    W --> WE[WorkflowExpression]
    W --> EX[WorkflowExecute.processRunExecutionData]
    EX --> STACK[nodeExecutionStack 循环]
    STACK --> RUN[runNode → executeNode]
    RUN --> PARAM[getNodeParameter 逐 item 解析表达式]
    PARAM --> DPROXY[WorkflowDataProxy]
    DPROXY --> EVAL[ExpressionEvaluator 编译+隔离执行]
    RUN --> DATA[main[to_index] = 输出数据[outputIndex]]
    RUN --> NEXT[addNodeToBeExecuted 下发下游]
    NEXT --> STACK
```

## 1. 加载链（零校验 + 默认参数填充）

入口：DB `WorkflowEntity`（`packages/@n8n/db/src/entities/workflow-entity.ts`，
nodes/connections/settings/pinData/staticData 均为 JSON 列）→ `workflowData`
→ `new Workflow({nodes, connections, settings, pinData, staticData, ...})`
（`packages/cli/src/workflow-runner.ts:371`、`scaling/job-processor.ts:156`）。

`Workflow` 构造函数（`packages/workflow/src/workflow.ts:58`）要点：

1. **未知节点 type 零校验**：`getByNameAndVersion(node.type, typeVersion)` 返回
   undefined 时 `continue` 跳过（注释明说"Go on to next node when its type is
   not known. For now do not error"，`workflow.ts:83-94`）。→ 与我们
   `node_class_for` 的 GenericNode 白名单一致。
2. **唯一规范化**：`NodeHelpers.getNodeParameters(description.properties,
   node.parameters, true, false, node, description)`（`workflow.ts:105-113`）按
   节点描述**补齐缺失参数的默认值、剥离未知参数**。加载即执行。
3. `setNodes/setConnections/setPinData/setSettings` 后：
   - `setConnections` 同时构建 `connectionsByDestinationNode`
     （`mapConnectionsByDestination`，`workflow.ts:129-132`）——**连接以源节点
     为索引**，反向索引用于找 parent。
   - `settings.timezone ?? defaultTimezone`；`staticData` 包成 ObservableObject。
   - `new WorkflowExpression(workflow)`（`workflow.ts:161`）。

## 2. 表达式链（= 前缀识别 → 模板编译 → 隔离执行）

识别：`isExpression = typeof string && charAt(0) === '='`
（`packages/workflow/src/expressions/expression-helpers.ts`）。**前缀 `=` 是
唯一识别符**；`{{ }}` 是模板定界符，不是识别符。

求值路径（`packages/workflow/src/workflow-expression.ts:31` 起）：
`resolveSimpleParameterValue` → `WorkflowDataProxy(workflow, runData, runIndex,
itemIndex, activeNodeName, connectionInputData, ...)` → `Expression.
resolveSimpleParameterValue` → `renderExpression`（`expression.ts:651-670`）。

引擎双模式（`expression.ts` 类级状态）：

- **legacy**：`@n8n/tournament`（`packages/@n8n/tournament/src/`）。模板切块
  `ExpressionSplitter.splitExpression`：`{{ }}` 内为 code 块、外部为 text 块，
  **未闭合 `{{` 容忍**（`hasClosingBrackets=false`）；`ExpressionBuilder` 把
  code 块包成 JS（`maybeWrapExpr`）后 `parse` 成 ESTree 再求值；text 块原样
  拼接。`=hello`（无 `{{ }}`）→ 全 text → 原样返回。
- **vm（默认，服务端）**：`@n8n/expression-runtime` 的 `ExpressionEvaluator`
  （`evaluator/expression-evaluator.ts:34`）：Tournament 把表达式**编译成 JS**
  并缓存（`codeCache`，命中 ~99.9%），在 `IsolatedVmBridge`（v8 isolate 池）
  隔离执行；`before/after` sanitizer（ThisSanitizer / PrototypeSanitizer /
  DollarSignValidator）防逃逸。错误分类见 `evaluator/error-classification.ts`。

上下文（`workflow-data-proxy.ts`）：`$json` = 当前输入 item 的 json
（`connectionInputData[itemIndex]`）；`$node["X"]` 从 runData 读 X 的输出，
`.json`/`.binary` 是数据访问器、`.params`/`.isExecuted` 是节点访问器
（`node-reference-parser-utils.ts:52-54`）；节点引用四模式
`$node["X"]` / `$node.X` / `$('X')` / `$items("X")`（ACCESS_PATTERNS，
`node-reference-parser-utils.ts:70-110`）。运行时 `$node.X.param` 访问节点
参数（非数据）。

**求值时机**：节点 execute 时按 item 循环调用（`getNodeParameter`），
`{{ }}` 内是任意 JS 表达式（算术/三元/函数调用都合法）。

## 3. 执行引擎链（栈驱动 + 端口路由 + 等齐）

`WorkflowExecute.processRunExecutionData`（`packages/core/src/execution-engine/
workflow-execute.ts:1577`）：

1. 初始化：`establishExecutionContext` → hook `workflowExecuteBefore`。
2. **主循环**：`nodeExecutionStack.shift()`（`workflow-execute.ts:1661-1664`），
   `ensureInputData` 检查输入，然后 `runNode` → `executeNode`。
3. **入口**：trigger/webhook 节点（无 main 输入）作为初始栈内容。
4. **输入合并** `prepareConnectionInputData`（`:946`）：常规节点只用
   `inputData.main[0]`（**第一个输入**）；`executionOrder !== 'v1'`（默认 v2）
   强制等所有输入，v1 取第一个有数据的输入。无输入数据 → `null`（节点不执行）。
5. **输出路由**（`:2218-2270`）：节点成功后遍历
   `connectionsBySourceNode[name].main` 的所有输出端口 `outputIndex`，
   **仅当 `nodeSuccessData[outputIndex]` 非空**才下发下游（例外：目标输入
   端口 `connectionData.index > 0` 且 legacy 顺序时即使空也执行）。
   v1 按 position 左上优先排序（`nodesToAdd.sort`，`:2301-2312`），v2 用
   `addNodeToBeExecuted`。
6. **多输入等齐** `addNodeToBeExecuted`（`:430`）：目标 `main` 输入端口数 >1
   时进 `waitingExecution`，按 `connectionData.index` 记槽位，**全部输入到齐
   才放行**；`v1 ? unshift : push` 决定栈/队列。
7. **数据写入**：`inputData.main[connectionData.index] = nodeSuccessData[outputIndex]`
   （`:519`）——目标输入端口索引 ↔ 源输出端口索引的映射在此完成。
8. 结束：`processSuccessExecution` → `IRun`（resultData.runData[node.name][runIndex]）。

**分支端口约定**：IF 节点 `outputs: [Main, Main]`（`If/V1/IfV1.node.ts:36`、
`V2/IfV2.node.ts:29`），execute 返回 `[[trueItems],[falseItems]]` → **true=端口
0（main_0）、false=端口 1（main_1）**。

## 4. 节点执行与 Code 节点运行时

`executeNode` → 节点的 `execute(this)`（IExecuteFunctions）。节点参数经
`getNodeParameter` 按 item 求值表达式；`$json` 指当前 item。

**Code 节点**（`packages/nodes-base/nodes/Code/`）：

- 用户 JS 包成 **`module.exports = async function() {<jsCode>\n}()`** 立即执行
  （`JavaScriptSandbox.ts:58`）→ 顶层 `return`/`await` 合法、顶层 `const` 是
  函数级作用域。
- 沙箱 **vm2 `NodeVM`**（`JavaScriptSandbox.ts:26-46`），`require` 经
  `vmResolver`（`makeResolverFromLegacyOptions`）：**builtin 白名单默认空
  （`NODE_FUNCTION_ALLOW_BUILTIN`）、external 默认关闭** → 默认配置下任何
  `require` 运行必失败。
- 新模式 `JsTaskRunnerSandbox`（task runner）用于 `runOnceForAllItems` /
  `runOnceForEachItem` 两 mode（`Code.node.ts:214-216`）。
- `runOnceForEachItem` 有 `validateNoDisallowedMethodsInRunForEach`（禁用方法
  白名单）。
- 返回校验：null → []；multiOutput（Switch）要求数组的数组。
- Python 模式（`pythonNative`）需要独立运行时（`N8N_PYTHON_ENABLED` 开关）。

## 5. 与数据模型

- 边：`connections[源].main[i][j] = {node, type, index}`（编辑器保存形状），
  `index` = **目标输入端口索引**；内部接口另有 `sourceIndex/destinationIndex`
  （`packages/workflow/src/interfaces.ts:450-454`）。
- 运行数据：`IRunData[nodeName][runIndex] = {data: {main: [...][...]}, source,
  executionStatus, ...}`；`pairedItem` 记录上游 item 归属（`:1700-1730`）。
- 多输出并存：`nodeSuccessData` 是 `INodeExecutionData[][]`（端口 × items）。

## 关键结论（编译器对拍基准）

1. 加载零校验：连接引用缺失节点、孤立节点、IF 未连全端口——编辑器/运行时
   全部容忍（`Workflow` 构造不检查连接完整性）。
2. `=` 前缀是表达式唯一识别符；`{{ }}` 是模板定界符，未闭合也接受。
3. 表达式求值发生在**每个节点 execute 时逐 item**，非加载时——语法错误
   runtime 才暴露（我们编译器前置为静态检查是增强，不是对拍）。
4. 边 `index` = 目标输入端口索引，运行时数据按它写槽（`main[to_index]`）。
5. IF true=0 / false=1；Switch 输出端口数 = 路由数（运行时确定）。
6. Code 节点 JS 是"async 函数体"语义：顶层 return/await 合法、import/export
   语法错误、require 默认全禁。
