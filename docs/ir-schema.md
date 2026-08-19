# n8n Typed IR v2 Schema

> Format: `n8n-typed-ir` / Version: `2` (v1 accepted for backward compatibility)
> Source: `typed_ir.py` — white-list fields, strict type loading, SHA-256 digest
> Updated: 2026-08-19

---

## 1. Overview

The typed IR is the output of the n8n compiler pipeline:

```
workflow JSON → parser → AST → checker → compiler → typed IR v2
```

It is a strict, self-validating representation of an n8n workflow. Every
consumer must validate the document through `load_typed_ir_json()` or
`validate_typed_ir()` before use — the digest is a SHA-256 of all semantic
fields and detects tampering or corruption.

**Key design decisions:**

- **Flattened**: n8n has no node hierarchy → `hierarchy` is always `{}`
- **Single scope**: `execution_order` has one scope `__root__` (Kahn topological
  order of main-dataflow nodes; AI subnodes are excluded — they are fetched at
  runtime by the agent)
- **Multi-entry**: `entry_keys` is a list (n8n supports multiple triggers:
  manual + errorTrigger, etc.)
- **Synthetic exit**: `exit_key` is always `__exit__` (a synthetic node type
  `synthetic.exit`)
- **JS first-class citizen**: Code nodes carry their static analysis result in
  `config.js` (contract + payload + errors/warnings)
- **AI sub-connections**: v2 carries all `ai_*` sub-connections in the
  `connections` array with `conn_type` distinguishing them

---

## 2. Top-level Document

```json
{
  "format": "n8n-typed-ir",
  "format_version": 2,
  "workflow": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "version": "1",
    "entry_keys": ["manual_trigger"],
    "exit_key": "__exit__",
    "settings": {
      "executionOrder": "v2",
      "saveManualExecutions": true,
      "timezone": "UTC"
    }
  },
  "nodes": [ ... ],
  "connections": [ ... ],
  "hierarchy": {},
  "execution_order": {
    "__root__": ["manual_trigger", "code_1", "if_1", "set_1", "__exit__"]
  },
  "manifest": { ... },
  "digest": "sha256:abcdef0123456789..."
}
```

### Top-level fields

| Field | Type | Required | Description |
|---|---|---|---|
| `format` | `string` | ✓ | Must be `"n8n-typed-ir"` |
| `format_version` | `int` | ✓ | `2` (current); `1` accepted for backward compatibility |
| `workflow` | `object` | ✓ | Workflow metadata (see §2.1) |
| `nodes` | `array` | ✓ | Non-empty array of node objects (see §3) |
| `connections` | `array` | ✓ | Array of connection objects (see §4) |
| `hierarchy` | `object` | ✓ | Always `{}` — n8n has no hierarchy |
| `execution_order` | `object` | ✓ | Single-scope topological order (see §5) |
| `manifest` | `object` | ✓ | Runtime dependency manifest (see §6) |
| `digest` | `string` | ✓ | `"sha256:"` + hex SHA-256 of all other fields, sorted keys, no `NaN` |

### 2.1 Workflow object

| Field | Type | Required | Description |
|---|---|---|---|
| `id` | `string` | ✓ | Workflow UUID (must be a valid string) |
| `version` | `string` | ✓ | Workflow version number (string per validation) |
| `entry_keys` | `string[]` | ✓ | Trigger node keys (may be empty for manual-only workflows) |
| `exit_key` | `string` | ✓ | Must be `"__exit__"` |
| `settings` | `object` | ✗ | n8n workflow settings (optional; v1 IR documents omit this) |

---

## 3. Node Object

```json
{
  "key": "code_1",
  "type": "n8n-nodes-base.code",
  "name": "Transform Data",
  "parent_key": null,
  "input_types": {
    "main": { "type": "any", "required": true }
  },
  "output_types": {
    "main": { "type": "any", "required": false }
  },
  "input_sources": [
    {
      "path": ["data", "value"],
      "source": {
        "ref": {
          "from_node_key": "trigger_1",
          "from_path": ["json"],
          "variable_type": null
        }
      }
    }
  ],
  "output_sources": [],
  "error_policy": {
    "on_error": "stopWorkflow",
    "retry_on_fail": false,
    "max_tries": 3,
    "wait_between_tries": 1000
  },
  "dependencies": {
    "direct": {
      "trigger_1": [
        { "from_path": ["json"], "to_path": ["data", "value"] }
      ]
    },
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
    "error_policy": { "on_error": "stopWorkflow" },
    "js": {
      "contract": {
        "deps": [{ "base": "items", "path": [] }],
        "output": { "kind": "any", "props": {}, "elem": null },
        "effect": "pure",
        "runtime": "external"
      },
      "payload": { "language": "js", "source": "return items;" },
      "errors": [],
      "warnings": []
    }
  }
}
```

### Node fields

| Field | Type | Required | Description |
|---|---|---|---|
| `key` | `string` | ✓ | Unique node identifier within this document |
| `type` | `string` | ✓ | n8n type name (e.g. `"n8n-nodes-base.code"`) |
| `name` | `string` | ✓ | Human-readable display name |
| `parent_key` | `string` | ✗ | Always `null` for n8n (no hierarchy) |
| `input_types` | `object` | ✗ | Input port type info, keyed by port name (see §3.2) |
| `output_types` | `object` | ✗ | Output port type info, keyed by port name |
| `input_sources` | `array` | ✗ | Expression binding sources (see §3.3) |
| `output_sources` | `array` | ✗ | Output field sources (may be empty) |
| `error_policy` | `object` | ✗ | Node-level error handling (see §3.4) |
| `dependencies` | `object` | ✗ | Dependency classification (see §3.5) |
| `config` | `object` | ✓ | Node configuration (see §3.6) |

### 3.2 Type info

```json
{ "type": "any", "required": true, "desc": "...", "elem_type_info": {}, "properties": {} }
```

| Field | Type | Required | Description |
|---|---|---|---|
| `type` | `string` | ✓ | One of: `any`, `string`, `number`, `boolean`, `list`, `object`, `binary` |
| `required` | `boolean` | ✗ | Whether this input is required |
| `desc` | `string` | ✗ | Human-readable description |
| `elem_type_info` | `object` | ✗ | Element type for `list` types (recursive) |
| `properties` | `object` | ✗ | Property types for `object` types (recursive, keyed by field name) |

### 3.3 Field info (input_sources / output_sources)

```json
{
  "path": ["data", "value"],
  "source": {
    "ref": {
      "from_node_key": "trigger_1",
      "from_path": ["json"],
      "variable_type": null
    }
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `path` | `string[]` | ✓ | Field path in the current node |
| `source` | `object` | ✓ | Either `{ "ref": {...} }` or `{ "literal": ... }` |
| `source.ref` | `object` | ✗ | Reference to another node's output |
| `source.ref.from_node_key` | `string` | ✓ | Source node key |
| `source.ref.from_path` | `string[]` | ✓ | Source field path |
| `source.ref.variable_type` | `string` | ✗ | Global variable type if applicable (see §3.7) |
| `source.literal` | `any` | ✗ | Literal value (not used in n8n — present for coze_compiler alignment) |

### 3.4 Error policy

```json
{ "on_error": "stopWorkflow", "retry_on_fail": false, "max_tries": 3, "wait_between_tries": 1000 }
```

| Field | Type | Required | Description |
|---|---|---|---|
| `on_error` | `string` | ✗ | One of: `"stopWorkflow"`, `"continueRegularOutput"`, `"continueErrorOutput"` |
| `retry_on_fail` | `boolean` | ✗ | Whether to retry on failure |
| `max_tries` | `int` | ✗ | Maximum retry attempts |
| `wait_between_tries` | `int` | ✗ | Milliseconds between retries |

### 3.5 Dependencies

```json
{
  "direct": { "trigger_1": [{ "from_path": ["json"], "to_path": ["data", "value"] }] },
  "indirect": {},
  "parent": {},
  "static_values": [],
  "variables": [{ "variable_type": "env", "from_path": ["API_KEY"], "to_path": ["api_key"] }]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `direct` | `object` | ✗ | Mappings from direct upstream nodes (keyed by source node key), each entry is an array of `{from_path, to_path}` |
| `indirect` | `object` | ✗ | Mappings from indirect (non-immediate) upstream nodes via `$node["X"]` references |
| `parent` | `object` | ✗ | Always empty for n8n (no hierarchy) |
| `static_values` | `array` | ✗ | Static literals — always empty for n8n (coze_compiler alignment) |
| `variables` | `array` | ✗ | Global variable bindings |

Each mapping entry:

```json
{ "from_path": ["json"], "to_path": ["data", "value"] }
```

| Field | Type | Required | Description |
|---|---|---|---|
| `from_path` | `string[]` | ✓ | Source field path |
| `to_path` | `string[]` | ✓ | Destination field path in current node |

### 3.6 Config

```json
{
  "kind": "code",
  "n8n_type": "n8n-nodes-base.code",
  "type_version": 2,
  "position": [0, 0],
  "parameters": { "jsCode": "return items;" },
  "credentials": { "openAiApi": { "id": "cred_123" } },
  "error_policy": { "on_error": "stopWorkflow" },
  "js": { ... },
  "js_ast": { ... }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `kind` | `string` | ✓ | Semantic classification: `trigger`, `http`, `if`, `code`, `llm`, `set`, `filter`, `limit`, `merge`, `respond`, `error_trigger`, `generic` |
| `n8n_type` | `string` | ✓ | Full n8n node type name |
| `type_version` | `int` | ✗ | Node type version (e.g. `1` or `2`) |
| `position` | `[int, int]` | ✗ | Editor coordinates `[x, y]` |
| `parameters` | `object` | ✗ | Node parameter values (may contain `{{ }}` expression templates) |
| `credentials` | `object` | ✗ | Credential references `{ "name": { "id": "..." } }` |
| `error_policy` | `object` | ✗ | Node-level error policy (same shape as §3.4) |
| `js` | `object` | ✗ | JS static analysis result for Code nodes (see §3.6.1) |
| `js_ast` | `object` | ✗ | Optional ESTree AST (first-class citizen representation) |

#### 3.6.1 JS contract

```json
{
  "contract": {
    "deps": [{ "base": "items", "path": [] }],
    "output": { "kind": "any", "props": {}, "elem": null },
    "effect": "pure",
    "runtime": "external"
  },
  "payload": { "language": "js", "source": "return items;" },
  "errors": [],
  "warnings": []
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `contract.deps` | `array` | ✗ | Static input dependencies: `{ "base": "items"|"$json"|"$input", "path": ["field", "sub"] }` |
| `contract.output` | `object` | ✗ | Output shape: `{ "kind": "void"|"object"|"list"|"any", "props": {}, "elem": "..." }` |
| `contract.effect` | `string` | ✗ | Side-effect classification: `"pure"`, `"io"`, `"unknown"` |
| `contract.runtime` | `string` | ✗ | Runtime mode: `"external"`, `"direct"`, `"static-only"` |
| `payload.language` | `string` | ✓ | Source language: `"js"` |
| `payload.source` | `string` | ✓ | Raw source code |
| `errors` | `string[]` | ✓ | Static analysis errors (empty = no errors) |
| `warnings` | `string[]` | ✓ | Static analysis warnings |

### 3.7 Global variable types

| `variable_type` | Description |
|---|---|
| `env` | Environment variable (`$env.KEY`) |
| `execution` | Execution context (`$execution.id`) |
| `workflow` | Workflow metadata (`$workflow.name`) |
| `now` | Current timestamp (`$now`) |
| `parameters` | Node parameters (`$parameter.name`) |
| `items` | Item stream (`$items("X")`) |

---

## 4. Connection Object

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

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `from_node` | `string` | ✓ | — | Source node key |
| `from_port` | `string` | ✓ | — | Source port: `"main"`, `"main_1"`, `"main_2"`, ... |
| `to_node` | `string` | ✓ | — | Target node key |
| `to_port` | `string` | ✓ | — | Target port: `"main"` (main edges); may be `conn_type` value for AI edges |
| `to_index` | `int` | ✗ | `0` | Target input port index (for multi-input nodes like Merge) |
| `conn_type` | `string` | ✗ | `"main"` | Connection type (see §4.1) |

### 4.1 Connection types

| `conn_type` | Description |
|---|---|
| `main` | Standard data flow |
| `ai_agent` | AI Agent sub-node connection |
| `ai_chain` | AI Chain sub-node connection |
| `ai_document` | Document sub-node connection |
| `ai_embedding` | Embedding model sub-node connection |
| `ai_languageModel` | Language model sub-node connection |
| `ai_memory` | Memory sub-node connection |
| `ai_outputParser` | Output parser sub-node connection |
| `ai_reranker` | Re-ranker sub-node connection |
| `ai_retriever` | Retriever sub-node connection |
| `ai_textSplitter` | Text splitter sub-node connection |
| `ai_tool` | Tool sub-node connection |
| `ai_vectorStore` | Vector store sub-node connection |

**Connection semantics:**

- `from_port` must match `^main(_[0-9]+)?$` — IF's false branch is `main_1`,
  Switch's Nth route is `main_N`
- `to_port` is `"main"` for main edges; AI edges may use their `conn_type` value
  as `to_port`
- `conn_type` must be `"main"` or start with `"ai_"` — unknown types are rejected
- `to_index` routes to the Nth input port on the target node (e.g., Merge uses
  `to_index=0` for left input, `to_index=1` for right input)

---

## 5. Execution Order

```json
{
  "__root__": ["manual_trigger", "code_1", "if_1", "set_1", "__exit__"]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `__root__` | `string[]` | ✓ | Kahn topological order of **main-dataflow nodes only** |

**Rules:**

- The single scope `__root__` contains all main-topology nodes — a permutation
  of the node set after excluding AI-only subnodes (nodes connected only via
  `ai_*` edges, with no `main` edges)
- AI subnodes are excluded because they are fetched at runtime by the agent,
  not scheduled by the main execution engine
- **This is not n8n's execution order** — n8n's runtime schedules by data
  arrival (item-level, merge-join semantics). The topological order is a
  compiler-level guarantee for dependency analysis only
- Duplicate keys are rejected
- Missing or extra keys relative to the main-topology set are rejected

---

## 6. Manifest

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
    "vector_stores": [{ "id": "InMemoryVectorStore" }],
    "tools": [{ "id": "my_workflow_tool" }],
    "webhooks": [{ "id": "webhook_1", "path": "/hook/abc", "httpMethod": "POST" }],
    "credentials": [{ "id": "openAiApi", "credential_id": "cred_123" }]
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `bind_status` | `object` | ✓ | All values are `"lazy_deferred"` (n8n resolves credentials/resources at runtime by name) |
| `ai_connections_dropped` | `int` | ✗ | Number of `ai_*` connections in the workflow (must be ≥ 0; v2 IR carries all connections, so this is a count, not a "dropped" count) |
| `requires` | `object` | ✓ | Runtime dependency groups |
| `requires.models` | `array` | ✗ | Model references extracted from `parameters.modelName` |
| `requires.vector_stores` | `array` | ✗ | Vector store references (from `memoryKey` or node name fallback) |
| `requires.tools` | `array` | ✗ | Tool references (from `ToolNode` node names) |
| `requires.webhooks` | `array` | ✗ | Webhook declarations: `{ "id", "path", "httpMethod" }` |
| `requires.credentials` | `array` | ✗ | Credential references: `{ "id", "credential_id" }` |

Each resource reference:

```json
{ "id": "resource_name", "credential_id": "optional_credential_uuid" }
```

---

## 7. Digest

The digest is computed as:

```
sha256:<SHA-256 of canonical JSON body>
```

Where the body is the full document with `digest` removed, serialized with:

- `sort_keys=True` (deterministic key ordering)
- `ensure_ascii=False` (preserves Unicode)
- `separators=(",", ":")` (compact, no whitespace)
- `allow_nan=False` (rejects NaN/Infinity — these are caught early by
  `reject_non_finite`)

Every consumer **must** verify the digest before using the document.

---

## 8. Validation Rules Summary

| Rule | Location | Description |
|---|---|---|
| R1 | Top-level | `format` must be `"n8n-typed-ir"`, `format_version` in `{1, 2}` |
| R2 | Workflow | `exit_key` must be `"__exit__"`, `entry_keys` must reference valid nodes |
| R3 | Nodes | Must contain exactly one `synthetic.exit` node; no duplicate keys |
| R4 | Config | `kind` and `n8n_type` are required; `js` must have `contract` + `payload` + `errors` + `warnings` |
| R5 | Types | Must be one of: `any`, `string`, `number`, `boolean`, `list`, `object`, `binary` |
| R6 | Connections | `from_port` must match `^main(_[0-9]+)?$`; `conn_type` must be `"main"` or start with `"ai_"` |
| R7 | Connections | `to_index` must be an integer when present; `to_port` must be `"main"` (or `conn_type` for AI edges) |
| R8 | Connections | All referenced nodes must exist in the node set |
| R9 | Execution order | Single scope `__root__` must be a permutation of main-topology nodes (excluding AI-only subnodes) |
| R10 | Manifest | `bind_status` and `requires` are required; `ai_connections_dropped` must be int ≥ 0 |
| R11 | Manifest | Each resource reference must have a string `id` |
| R12 | Digest | Format `"sha256:..."` with matching hex checksum; verified after all other validations |
| R13 | NaN | JSON `NaN`/`Infinity`/`-Infinity` are rejected at parse time |

---

## 9. Version Compatibility

| Version | Changes | Document shape |
|---|---|---|
| v1 (legacy) | Initial IR format | No `conn_type` on connections (defaults to `"main"`); no `settings` in workflow; no AI sub-connection support |
| v2 (current) | AI sub-connections, settings, JS first-class | `conn_type` on connections; `workflow.settings` optional; Code `config.js` + `config.js_ast` |

v1 documents are accepted by v2 validators with `conn_type` defaulting to
`"main"` and `settings` absent. The reverse is not guaranteed.

---

## 10. Quick Reference: Minimal Valid Document

```json
{
  "format": "n8n-typed-ir",
  "format_version": 2,
  "workflow": {
    "id": "wf-1",
    "version": "1",
    "entry_keys": ["trigger"],
    "exit_key": "__exit__"
  },
  "nodes": [
    {
      "key": "trigger",
      "type": "synthetic.entry",
      "name": "Entry",
      "config": { "kind": "trigger", "n8n_type": "synthetic.entry" }
    },
    {
      "key": "__exit__",
      "type": "synthetic.exit",
      "name": "Exit",
      "config": { "kind": "generic", "n8n_type": "synthetic.exit" }
    }
  ],
  "connections": [
    { "from_node": "trigger", "from_port": "main", "to_node": "__exit__", "to_port": "main" }
  ],
  "hierarchy": {},
  "execution_order": {
    "__root__": ["trigger", "__exit__"]
  },
  "manifest": {
    "bind_status": {},
    "requires": {}
  },
  "digest": "sha256:..."
}
```
