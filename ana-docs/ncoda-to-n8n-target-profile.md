# ncoda → n8n Target Profile：语言能力矩阵、节点映射表、IR Schema

> 基准版本：n8n 2026-08-18 master / n8n_compiler IR v2（P1-1c 闭环）
> 对照：ncoda L6 → Dify / Coze 后端映射（/home/dev/godcc）
> 编制日期：2026-08-19

---

## 1. 映射总览

| 层 | ncoda | Dify 后端 | Coze 后端 | n8n 后端 |
|---|---|---|---|---|
| 入口 | `workflow` / `trigger` | 单 trigger→webhook/schedule | Canvas 入口节点 | 多 trigger 并置（manual/webhook/schedule/errorTrigger/chatTrigger/MCP） |
| 数据流 | `http` / `code` / `set` / `filter` / `limit` | 对应 API/Code/Assigner/Filter/Iterator | 对应 Code/LLM/Plugin | 方向一致，但 n8n 用边 `index` 路由多输入端口 |
| 控制流 | `if` / `switch` / `for` / `parallel` | IF / Switch / Iteration / Parallel | IF / Switch / 循环 / 并行 | IF / Switch / SplitInBatches / 无原生并行 |
| AI 链 | `llm` / `model` / `tool` / `memory` / `retriever` / `vector_store` / `output_parser` | LLM / ModelConfig / Tool / Dataset / KnowledgeRetrieval / 无 | LLM / Model / Plugin / Knowledge / 无 | Agent / ChainLLM / Model / Tool / Memory / VectorStore / Retriever / OutputParser |
| 错误处理 | `attempt` / `on_error` | 无 | 无 | errorTrigger + errorWorkflow settings + 节点级 error_policy |

**差异要点**：

- n8n 允许多 trigger（manual + errorTrigger 等），ncoca 预设单 trigger → 编译器需处理 entry_keys 列表
- n8n 无原生 `parallel` 结构（Coze 有 Canvas 并行分支）→ ncoda `parallel` 降级为顺序或 `$items` 多路
- n8n 的 `for` 循环用 `splitInBatches` 节点（迭代器模式），非 Coze 的 Loop 容器
- n8n 的 AI 链用 `ai_*` 子连接（`ai_languageModel` / `ai_tool` / `ai_memory` 等），非 containment 结构

---

## 2. 语言能力矩阵

### 2.1 入口（Trigger）映射

| ncoda 构造 | n8n 节点类型 | 映射说明 | 实现状态 |
|---|---|---|---|
| `trigger manual` | `n8n-nodes-base.manualTrigger` | 1:1，无参数 | ✅ 注册表覆盖 |
| `trigger webhook(path, method)` | `n8n-nodes-base.webhook` | parameters.path / httpMethod | ✅ 注册表覆盖 |
| `trigger schedule(cron)` | `n8n-nodes-base.scheduleTrigger` | parameters.rule.interval=cron | ✅ 注册表覆盖 |
| `trigger error` | `n8n-nodes-base.errorTrigger` | 工作流错误入口 | ✅ 注册表覆盖 |
| `trigger chat` | `@n8n/n8n-nodes-langchain.chatTrigger` | AI Chat 入口 | ✅ 注册表覆盖 |
| `trigger mcp` | `@n8n/n8n-nodes-langchain.mcpTrigger` | MCP Server 入口 | ✅ 注册表覆盖（P2-1 v4） |
| `trigger form` | `n8n-nodes-base.formTrigger` | Form 入口 | ✅ 注册表覆盖（P2-1 v4） |
| `trigger executeWorkflow` | `n8n-nodes-base.executeWorkflowTrigger` | 被其他工作流调用时触发 | ✅ 注册表覆盖（P2-1 v4） |

### 2.2 数据流节点映射

| ncoda 构造 | n8n 节点类型 | 映射说明 | 特殊处理 |
|---|---|---|---|
| `http(method, url, ...)` | `n8n-nodes-base.httpRequest` | parameters.method / url / options | 表达式参数转 `={{ }}` 模板 |
| `code(body)` | `n8n-nodes-base.code` | 一等公民 JS 静态编译 | **第一等公民**：acorn 语法 → Contract → IR |
| `code(body)` [AI 链内联] | `@n8n/n8n-nodes-langchain.code` | supplyData 工厂（P1-2） | 取源 `parameters.code.supplyData.code` |
| `set(fields)` | `n8n-nodes-base.set` | parameters.values / options | 字段保持模式 vs 覆盖模式 |
| `filter(condition)` | `n8n-nodes-base.filter` | parameters.conditions | 表达式编译 |
| `limit(n)` | `n8n-nodes-base.limit` | parameters.maxItems | 字面量 |
| `merge(left, right, mode)` | `n8n-nodes-base.merge` | parameters.mode（combine/merge/...） | 多输入端口 to_index 保留 |
| `splitOut(field)` | `n8n-nodes-base.splitOut` | parameters.fieldToSplitOut | 字段名 |
| `respond(body)` | `n8n-nodes-base.respondToWebhook` | parameters.respondMode / content | Webhook 响应 |

### 2.3 控制流映射

| ncoda 构造 | n8n 节点类型 | 映射说明 | 特殊处理 |
|---|---|---|---|
| `if(condition) → then / else` | `n8n-nodes-base.if` | 条件编译为 `={{ }}` 表达式 | true=main_0 / false=main_1 |
| `switch(value) → cases` | `n8n-nodes-base.switch` | dataType=string / routing 规则 | 输出端口数 = case 数（运行时确定） |
| `for(item in list) → body` | `n8n-nodes-base.splitInBatches` | 迭代器模式（分批） | 循环体节点包在 batch 内 |
| `parallel → branches` | 无原生支持 | 降级为顺序 | 或用 `$items` 多路 + `merge` 聚合 |
| `attempt → body + on_error` | errorTrigger + error_policy | 节点级 error_policy | on_error = continueRegularOutput / continueErrorOutput |

### 2.4 AI 链映射

| ncoda 构造 | n8n 节点类型 | 子连接类型 | 参数映射 |
|---|---|---|---|
| `llm(prompt, model, ...)` | `@n8n/n8n-nodes-langchain.agent` 或 `chainLlm` | ai_languageModel / ai_tool / ai_memory | prompt → parameters.prompt / modelName → parameters.modelName |
| `model(name, ...)` | `@n8n/n8n-nodes-langchain.lmChat*` / `embeddings*` | ai_languageModel（子节点） | modelName / apiKey / baseURL |
| `tool(workflow, ...)` | `@n8n/n8n-nodes-langchain.toolWorkflow` | ai_tool（子节点） | source=workflowId / parameters |
| `memory(config)` | 多种 Memory 节点 | ai_memory（子节点） | memoryKey / sessionId / contextWindowLength |
| `retriever(config)` | `@n8n/n8n-nodes-langchain.retrieverVectorStore*` | ai_retriever（子节点） | vectorStore 引用 |
| `vector_store(config)` | `@n8n/n8n-nodes-langchain.vectorStore*` | 无（AI 子节点，被 retriever/tool 引用） | embedding / memoryKey |
| `output_parser(schema)` | `@n8n/n8n-nodes-langchain.outputParser*` | ai_outputParser（子节点） | schema / parsing 逻辑 |

> **AI 子连接类型**：n8n 用 `NodeConnectionType` 枚举（`packages/workflow/src/interfaces.ts`），
> 编译器 `node_type.py` 的 `AI_CONNECTION_TYPES` 覆盖：`ai_agent` / `ai_languageModel` /
> `ai_tool` / `ai_memory` / `ai_retriever` / `ai_outputParser` / `ai_data` / `ai_embedding` /
> `ai_document` / `ai_textSplitter`。IR v2 完整携带（P1-1c 闭环），不丢弃。

### 2.5 表达式与全局变量映射

| ncoda 表达式 | n8n 等价 | 映射说明 | 实现状态 |
|---|---|---|---|
| `$json.field` | `{{ $json.field }}` | 当前 item 字段 | ✅ parser 支持 |
| `$node("X").json.field` | `{{ $node["X"].json.field }}` | 跨节点数据引用 | ✅ parser 支持（.json 访问器限界） |
| `$node("X").param` | `{{ $node["X"].param }}` | 节点参数引用（非数据） | ✅ parser 标记为 UNKNOWN（保留原串） |
| `$env("KEY")` | `{{ $env.KEY }}` | 环境变量 | ✅ 全局变量绑定 |
| `$execution.id` | `{{ $execution.id }}` | 执行上下文 | ✅ 全局变量绑定 |
| `$now` | `{{ $now }}` | 当前时间 | ✅ 全局变量绑定 |
| `$workflow.name` | `{{ $workflow.name }}` | 工作流属性 | ✅ 全局变量绑定 |
| `$items("X").json.field` | `{{ $items("X").json.field }}` | 指定节点所有输出 | ❌ 待实现（`$items` 引用） |
| `$('X')` | `{{ $('X').json.field }}` | 节点引用快捷方式 | ❌ 待实现（parser 标 UNKNOWN） |

### 2.6 错误处理映射

| ncoda 构造 | n8n 等价 | 说明 |
|---|---|---|
| `attempt { body } on_error { handler }` | errorTrigger + 节点级 error_policy | on_error = continueRegularOutput（继续）或 stopWorkflow（终止） |
| 节点级 `retry` | `error_policy.retry_on_fail` + `max_tries` + `wait_between_tries` | 重试策略 |
| 工作流级错误 | `settings.errorWorkflow` | 错误时调用另一个工作流 |

---

## 3. 节点映射表（ncoda → n8n NodeType）

### 3.1 核心映射

| ncoda 语义 | n8n type（注册表键） | NodeKind | 输入端口 | 输出端口 | 形状 |
|---|---|---|---|---|---|
| entry/trigger | `n8n-nodes-base.manualTrigger` | TRIGGER | 0 | 1 | trigger |
| entry/trigger | `n8n-nodes-base.webhook` | TRIGGER | 0 | 1 | trigger |
| entry/trigger | `n8n-nodes-base.scheduleTrigger` | TRIGGER | 0 | 1 | trigger |
| entry/trigger | `n8n-nodes-base.errorTrigger` | ERROR_TRIGGER | 0 | 1 | trigger |
| entry/trigger | `@n8n/n8n-nodes-langchain.chatTrigger` | TRIGGER | 0 | 1 | trigger |
| http | `n8n-nodes-base.httpRequest` | HTTP | 1 | 1 | any |
| if | `n8n-nodes-base.if` | IF | 1 | 2 | branch |
| code | `n8n-nodes-base.code` | CODE | 1 | 1 | any |
| code (AI) | `@n8n/n8n-nodes-langchain.code` | CODE | 1 | 1 | any |
| filter | `n8n-nodes-base.filter` | FILTER | 1 | 1 | identity |
| limit | `n8n-nodes-base.limit` | LIMIT | 1 | 1 | identity |
| set | `n8n-nodes-base.set` | SET | 1 | 1 | object |
| merge | `n8n-nodes-base.merge` | MERGE | 2 | 1 | merge |
| splitOut | `n8n-nodes-base.splitOut` | SPLIT_OUT | 1 | 1 | identity |
| respond | `n8n-nodes-base.respondToWebhook` | RESPOND | 1 | 0 | sink |
| llm | `@n8n/n8n-nodes-langchain.agent` | LLM | 1 | 1 | any |
| llm | `@n8n/n8n-nodes-langchain.chainLlm` | LLM | 1 | 1 | any |
| model | `@n8n/n8n-nodes-langchain.lmChat*` | MODEL | 0 | 1 | any |
| tool | `@n8n/n8n-nodes-langchain.toolWorkflow` | TOOL | 0 | 1 | any |
| memory | `@n8n/n8n-nodes-langchain.memory*` | MEMORY | 0 | 1 | any |
| retriever | `@n8n/n8n-nodes-langchain.retrieverVectorStore*` | RETRIEVER | 0 | 1 | any |
| vector_store | `@n8n/n8n-nodes-langchain.vectorStore*` | VECTOR_STORE | 0 | 1 | any |
| output_parser | `@n8n/n8n-nodes-langchain.outputParser*` | OUTPUT_PARSER | 0 | 1 | any |
| switch | `n8n-nodes-base.switch` | GENERIC | 1 | 4 | branch |
| splitInBatches | `n8n-nodes-base.splitInBatches` | GENERIC | 1 | 2 | branch |
| executeWorkflow | `n8n-nodes-base.executeWorkflow` | GENERIC | 1 | 1 | any |
| extractFromFile | `n8n-nodes-base.extractFromFile` | GENERIC | 1 | 1 | any |

### 3.2 端口命名约定

| 概念 | 编码 | 说明 |
|---|---|---|
| main 输入端口 0 | `main` | 单输入节点的默认端口 |
| main 输入端口 N | `main`（to_index=N） | 多输入节点（如 Merge）的端口索引 |
| main 输出端口 0 | `main` | 单输出节点的默认端口 |
| main 输出端口 1-N | `main_1` ... `main_N` | IF 的 false 分支（main_1）、Switch 多路 |
| AI 子连接 | `ai_languageModel` / `ai_tool` / ... | 非 main 连接，conn_type 编码 |

### 3.3 节点参数映射

| ncoda 参数 | 目标 n8n parameters 路径 | 映射方式 |
|---|---|---|
| `http.url` | `parameters.url` | 表达式转 `={{ }}` 模板 |
| `http.method` | `parameters.method` | 枚举值映射 |
| `http.options` | `parameters.options` | 嵌套 dict |
| `code.source` | `parameters.jsCode` | 一等公民 JS 编译器 |
| `if.condition` | `parameters.conditions` | IF 枚举条件结构 |
| `set.fields` | `parameters.values` | 键值对列表 |
| `filter.condition` | `parameters.conditions` | Filter 条件结构 |
| `limit.n` | `parameters.maxItems` | 字面量整数 |
| `merge.mode` | `parameters.mode` | 枚举值映射 |
| `llm.prompt` | `parameters.prompt` | 消息结构 |
| `llm.model` | `parameters.modelName` / `options.model` | 模型引用名 |
| 未映射参数 | 保留原始 dict | `parameters` 原样传递 |

---

## 4. IR Schema（typed IR v2）

### 4.1 顶层结构

```json
{
  "format": "n8n-typed-ir",
  "format_version": 2,
  "workflow": {
    "id": "<workflow uuid>",
    "version": 1,
    "entry_keys": ["<trigger_key>", ...],
    "exit_key": "__exit__",
    "settings": {
      "executionOrder": "v2",
      "saveManualExecutions": true,
      "timezone": "UTC",
      ...
    }
  },
  "nodes": [ ... ],
  "connections": [ ... ],
  "hierarchy": {},
  "execution_order": {
    "__root__": ["trigger_key", "node_a", "node_b", "__exit__"]
  },
  "manifest": { ... },
  "digest": "sha256:<64-char-hex>"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `format` | string | 是 | 常量 `"n8n-typed-ir"` |
| `format_version` | int | 是 | 当前 2；v1 兼容装载（`_ACCEPTED_VERSIONS = {1, 2}`） |
| `workflow.id` | string | 是 | 工作流 UUID（deploy 时自动生成或导入保持） |
| `workflow.entry_keys` | string[] | 是 | trigger 节点 key 列表（可多个） |
| `workflow.exit_key` | string | 是 | 合成 `__exit__` |
| `workflow.settings` | object | 否 | n8n 工作流设置（v1 文档无此字段，v2 可选携带） |
| `nodes` | Node[] | 是 | 节点数组（见 4.2） |
| `connections` | Connection[] | 是 | 边数组（见 4.3） |
| `hierarchy` | object | 是 | 恒空 `{}`（n8n 无层级） |
| `execution_order` | object | 是 | `{ "__root__": [node_key, ...] }`，单 scope 的 Kahn 拓扑序 |
| `manifest` | Manifest | 是 | 运行时依赖清单（见 4.4） |
| `digest` | string | 是 | `sha256:` 前缀的 SHA-256 摘要 |

### 4.2 节点（Node）Schema

```json
{
  "key": "node_1",
  "type": "n8n-nodes-base.code",
  "name": "Transform",
  "parent_key": null,
  "input_types": {
    "main": { "type": "any", "required": true }
  },
  "output_types": {
    "main": { "type": "any", "required": false }
  },
  "input_sources": [
    { "path": ["data", "field1"], "source": { "ref": { "from_node_key": "trigger_1", "from_path": ["json"], "variable_type": null } } }
  ],
  "output_sources": [],
  "error_policy": {
    "on_error": "stopWorkflow",
    "retry_on_fail": false,
    "max_tries": 3,
    "wait_between_tries": 1000
  },
  "dependencies": {
    "direct": { "trigger_1": [{ "from_path": ["json"], "to_path": ["data", "field1"] }] },
    "indirect": {},
    "parent": {},
    "static_values": [],
    "variables": []
  },
  "config": {
    "kind": "code",
    "n8n_type": "n8n-nodes-base.code",
    "type_version": 2,
    "position": [0, 0],
    "parameters": { "jsCode": "return items;" },
    "credentials": {},
    "error_policy": { ... },
    "js": {                              //  第一等公民 JS 静态分析结果
      "contract": {
        "deps": [{ "base": "items", "path": [] }],
        "output": { "kind": "any", "props": {}, "elem": null },
        "effect": "pure",
        "runtime": "external"
      },
      "payload": { "language": "js", "source": "return items;" },
      "errors": [],
      "warnings": []
    },
    "js_ast": [ ... ]                     // 可选：ESTree AST 节点（第一等公民）
  }
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | string | 是 | 节点唯一标识符 |
| `type` | string | 是 | n8n 节点类型全名（如 `n8n-nodes-base.code`） |
| `name` | string | 是 | 用户可读名称 |
| `parent_key` | string | 否 | 恒 null（n8n 无层级） |
| `input_types` | object | 是 | `{ "main": { type, required } }` |
| `output_types` | object | 是 | `{ "main": { type, required } }` |
| `input_sources` | FieldInfo[] | 是 | 表达式绑定来源 |
| `output_sources` | FieldInfo[] | 是 | 输出字段来源（当前为空） |
| `error_policy` | object | 否 | 节点级错误处理策略 |
| `dependencies` | object | 是 | 依赖分类（direct/indirect/parent/static_values/variables） |
| `config` | object | 是 | 节点配置（见 4.2.1） |

#### 4.2.1 Config 子 schema

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | string | 是 | 语义分类（`code` / `trigger` / `http` / `if` / `llm` / ...） |
| `n8n_type` | string | 是 | 完整 n8n 节点类型 |
| `type_version` | int | 否 | 节点版本（如 1 / 2） |
| `position` | [int, int] | 是 | 编辑器坐标 `[x, y]` |
| `parameters` | object | 是 | 节点参数原值（含 `={{ }}` 表达式模板） |
| `credentials` | object | 否 | 凭据 `{ "credentialName": { "id": "xxx" } }` |
| `error_policy` | object | 否 | 节点级错误策略 |
| `js` | object | 否 | Code 节点静态分析结果（Contract + Payload） |
| `js_ast` | list | 否 | 可选：ESTree JSON AST（第一等公民） |

### 4.3 连接（Connection）Schema

```json
{
  "from_node": "trigger_1",
  "from_port": "main",
  "to_node": "code_1",
  "to_port": "main",
  "to_index": 0,
  "conn_type": "main"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 默认值 | 说明 |
|---|---|---|---|---|
| `from_node` | string | 是 | — | 源节点 key |
| `from_port` | string | 是 | — | 源端口（`main` / `main_1` / `main_2` / ...） |
| `to_node` | string | 是 | — | 目标节点 key |
| `to_port` | string | 是 | — | 目标端口（`main`；AI 子连接可为其 conn_type） |
| `to_index` | int | 否 | 0 | 目标输入端口索引（Merge 多输入等齐） |
| `conn_type` | string | 否 | `"main"` | 连接类型（`main` / `ai_agent` / `ai_languageModel` / `ai_tool` / ...） |

**连接类型列表**（n8n `NodeConnectionTypes` 枚举，`packages/workflow/src/interfaces.ts`）：

| conn_type | 说明 |
|---|---|
| `main` | 标准数据流连接 |
| `ai_agent` | AI Agent 子节点连接 |
| `ai_languageModel` | 语言模型子节点连接 |
| `ai_tool` | 工具子节点连接 |
| `ai_memory` | 记忆子节点连接 |
| `ai_retriever` | 检索器子节点连接 |
| `ai_outputParser` | 输出解析器子节点连接 |
| `ai_data` | 数据子节点连接 |
| `ai_chain` | AI Chain 子节点连接 |
| `ai_reranker` | 重排序器子节点连接 |
| `ai_vectorStore` | 向量库子节点连接 |
| `ai_embedding` | 嵌入模型子节点连接 |
| `ai_document` | 文档子节点连接 |
| `ai_textSplitter` | 文本分割器子节点连接 |

### 4.4 Manifest Schema

```json
{
  "bind_status": {
    "model": "lazy_deferred",
    "vector_store": "lazy_deferred",
    "tool": "lazy_deferred",
    "webhook": "not_required",
    "credential": "lazy_deferred"
  },
  "ai_connections_dropped": 0,
  "requires": {
    "models": [{ "id": "gpt-4o" }],
    "vector_stores": [{ "id": "pinecone" }],
    "tools": [{ "id": "my_tool" }],
    "webhooks": [{ "id": "webhook_1", "path": "/hook/123", "httpMethod": "POST" }],
    "credentials": [{ "id": "openAiApi", "credential_id": "xxx" }]
  }
}
```

**字段说明**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `bind_status.*` | string | 全部 `lazy_deferred`（n8n 运行时按名称解析，无 eager-bind） |
| `ai_connections_dropped` | int | 非 main 连接总数（v2 起全部携带，0 表示无丢弃） |
| `requires.models` | ResourceRef[] | 模型引用（从 parameters.modelName 提取） |
| `requires.vector_stores` | ResourceRef[] | 向量库引用（从 memoryKey 或节点名兜底） |
| `requires.tools` | ResourceRef[] | 工具引用（从 ToolNode 节点名） |
| `requires.webhooks` | ResourceRef[] | Webhook 声明（path + httpMethod） |
| `requires.credentials` | ResourceRef[] | 凭据引用（credentials dict 的凭据名） |

---

## 5. 与 Dify/Coze 的差异总结

| 维度 | Dify | Coze | n8n |
|---|---|---|---|
| 工作流格式 | YAML（多文件） | Canvas JSON（单文件） | JSON（单文件，nodes + connections） |
| 入口 | 单 trigger（webhook/schedule） | 单入口节点 | 多 trigger 可并置 |
| 层级 | 嵌套 Block | 嵌套 Container | 扁平（无层级） |
| AI 子节点 | 含在 LLM 节点的 model_config 内 | 含在 LLM 节点配置内 | 独立节点 + ai_* 子连接 |
| 凭据绑定 | 运行时按名称 | 运行时按名称 | 运行时按名称（lazy_deferred） |
| 表达式 | `{{ }}` 模板 + Jinja2 | `{{ }}` 模板 + 自定义函数 | `{{ }}` 模板 + JS 表达式 |
| 代码节点 | Python/Ruby 脚本 | Python 脚本 | JS（一等公民）+ Python（可选） |
| 循环 | Iteration Block | Loop 容器 | splitInBatches 节点 |
| 并行 | Parallel Block | 并行分支 | 无原生并行（降级顺序） |
| 错误处理 | 无内置 | 无内置 | errorTrigger + error_policy + errorWorkflow |
| 类型系统 | 9 种物理类型 | 有限类型 | 弱类型（Any 为主） |
| 图结构 | Block 嵌套 | 嵌套 Container | 纯 DAG 扁平 |

---

## 6. 已知限制与盲区

### 6.1 功能级盲区

| 盲区 | 影响 | 状态 |
|---|---|---|
| `$('X')` 节点引用快捷语法 | 表达式解析标 UNKNOWN，保留原串 | ❌ 待实现 |
| `$items("X")` 多行输出引用 | 同上 | ❌ 待实现 |
| langchain.code execute 变体（Main 输出） | 双形态只支持 supplyData | ⚠️ P2-1（v5 已记载） |
| v1 文档兼容装载（settings 无字段） | 真 v1 文档被 IR 校验拒绝 | ⚠️ P1-1（v5 已记载） |
| Switch 运行时输出端口数 | 无法静态确定，端口形状放行 | ⚠️ 有意保留 |
| 表达式 `=abc`（无 `{{ }}`） | 视为字面量（非表达式） | ⚠️ 有意保留（安全侧） |
| 节点参数默认值填充 | 无节点描述库，不补默认值 | ⚠️ 有意保留（v1 不做） |
| `require` 静态检查 | 标 warning 未升 error | ⚠️ 待决策（v5 建议） |

### 6.2 IR 级盲区

| 盲区 | 影响 | 说明 |
|---|---|---|
| settings 无白名单/值域校验 | 未知键 deploy 400 | P2-2（v5 已记载，编译器只 warning 不阻断） |
| 拓扑序 = Kahn 非 n8n 执行序 | 消费方不得把 execution_order 当 n8n 执行序 | 文档已注明（typed_ir.py 文件级注释） |
| 凭据无 id 映射 | deploy 时凭据名→id 需运行时映射 | runtime/deploy.py 已实现（P2-3 v4） |

---

## 7. 附录：编译器规格对照

| 科目 | coze_compiler（参考） | n8n_compiler（当前） | 对齐度 |
|---|---|---|---|
| 工作流 JSON 解析 | `parser/`（Pydantic 模型） | `parser/`（Python 标准库） | ✅ 功能等价 |
| 强类型 AST | `ast_nodes/`（NodeDecl 层次） | `ast_nodes/`（NodeDecl 层次） | ✅ 结构对齐 |
| 类型系统 | `type_system/`（TypeInfo + DataType） | `type_system/`（TypeInfo + DataType） | ✅ 结构对齐 |
| 作用域 | `scope/`（Scope + SymbolTable） | `scope/`（Scope + SymbolTable） | ✅ 结构对齐 |
| 值/引用 | `values/`（FieldInfo + Reference） | `values/`（FieldInfo + Reference） | ✅ 结构对齐 |
| 静态检查 | `checker/`（Validator） | `checker/`（Validator） | ✅ 功能等价 |
| 编译 IR | `compiler/`（workflow + dependency） | `compiler/`（workflow + dependency） | ✅ 结构对齐 |
| 一等公民语言 | 无（Coze 无 Code 一等公民） | `jscode/`（acorn + Contract + ESTree） | 🔧 n8n 特有增强 |
| 运行时反编译 | 无 | `runtime/decompile.py` | 🔧 n8n 特有增强 |
| 部署 | 无 | `runtime/deploy.py` | 🔧 n8n 特有增强 |
| 矩阵测试 | 无 | `tests/test_batch_matrix.py`（143 文件） | 🔧 n8n 特有增强 |
| 覆盖率门禁 | 无 | `tests/coverage.py`（90% 阈值） | 🔧 增强 |
| 验证脚本 | 无 | `scripts/execute_verify.py` | 🔧 增强 |
| 扩展节点集 | 150+ Coze 节点 | 1500+ n8n 开放节点（白名单 30+ 核心） | ⚠️ 长尾泛型覆盖 |
| 凭据映射 | 无 | manifest credentials lazy_deferred | ⚠️ 运行时按名称解析 |
| AI 子连接 | 嵌套在 LLM 配置内 | 独立节点 + ai_* 子连接 | 🔧 n8n 架构差异 |
| 连接类型 | main 单一类型 | main + 10 种 AI 连接类型 | 🔧 n8n 架构差异 |
