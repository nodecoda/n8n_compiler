"""编译产物运行时依赖清单 — 对齐 coze_compiler.manifest。

n8n 适配点：n8n 无 eager-bind（凭据/资源均在运行时按名称解析），全部 lazy_deferred；
webhook 是工作流声明的 API 面（declared）。资源 id 从节点参数提取：
  - models:        parameters.modelName（lmChat / embeddings / chainLlm 上的模型名）
  - vector_stores: n8n-nodes-langchain.vectorStore* （memoryKey 或节点名兜底）
  - tools:         toolWorkflow 等 ToolNode（节点名兜底）
  - webhooks:      n8n-nodes-base.webhook 的 path + httpMethod
  - credentials:   节点级 credentials dict 的凭据名
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ast_nodes.node_decls import (
    ToolNode,
    TriggerNode,
)
from ast_nodes.nodes import WorkflowAST

_MODEL_PARAM_KEYS = ("modelName", "model")
_WEBHOOK_TYPES = {"n8n-nodes-base.webhook", "n8n-nodes-base.webhookV2"}


@dataclass
class ResourceRef:
    """资源引用。"""
    id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, **self.metadata}


@dataclass
class DependencyManifest:
    """运行时依赖清单。"""
    bind_status: dict[str, str] = field(default_factory=lambda: {
        "model": "lazy_deferred",
        "vector_store": "lazy_deferred",
        "tool": "lazy_deferred",
        "webhook": "not_required",
        "credential": "lazy_deferred",
    })
    models: list[ResourceRef] = field(default_factory=list)
    vector_stores: list[ResourceRef] = field(default_factory=list)
    tools: list[ResourceRef] = field(default_factory=list)
    webhooks: list[ResourceRef] = field(default_factory=list)
    credentials: list[ResourceRef] = field(default_factory=list)
    # P1-1a（v4）引入、P1-1c（v4）语义更新：非 main 连接（ai_languageModel 等）
    # 总边数。字段名保留 v1 遗留（消费方兼容），v2 起 IR 完整携带这些边——
    # 本字段语义为「携带的 ai 边数」，不再有结构性丢弃（>0 = 工作流含 AI 链）。

    def to_dict(self) -> dict[str, Any]:
        return {
            "bind_status": dict(self.bind_status),
            "ai_connections_dropped": self.ai_connections_dropped,
            "requires": {
                "models": [item.to_dict() for item in self.models],
                "vector_stores": [item.to_dict() for item in self.vector_stores],
                "tools": [item.to_dict() for item in self.tools],
                "webhooks": [item.to_dict() for item in self.webhooks],
                "credentials": [item.to_dict() for item in self.credentials],
            },
        }


def _first_string(parameters: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = parameters.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _is_vector_store_type(n8n_type: str) -> bool:
    """langchain 向量库节点（注册表未覆盖的也命中）。"""
    return "vectorStore" in n8n_type or n8n_type.endswith(".retrieverVectorStore")


def _is_tool_type(n8n_type: str) -> bool:
    return n8n_type.endswith(".toolWorkflow")


def _collect_credentials(ast: WorkflowAST) -> list[ResourceRef]:
    result: list[ResourceRef] = []
    seen: set[str] = set()
    for node in ast.nodes.values():
        for credential_name, spec in node.credentials.items():
            if credential_name in seen:
                continue
            seen.add(credential_name)
            metadata: dict[str, Any] = {}
            if isinstance(spec, dict) and spec.get("id"):
                metadata["credential_id"] = spec["id"]
            result.append(ResourceRef(id=credential_name, metadata=metadata))
    return result


def build_manifest(ast: WorkflowAST) -> DependencyManifest:
    """构建 manifest。"""
    manifest = DependencyManifest()
    manifest.ai_connections_dropped = sum(ast.non_main_connections.values())
    models: set[str] = set()
    vector_stores: set[str] = set()
    tools: set[str] = set()
    webhooks: list[ResourceRef] = []

    for key, node in ast.nodes.items():
        # 模型/向量库/工具：形状级提取（不依赖注册表强类型，GenericNode 也命中）
        model = _first_string(node.parameters, _MODEL_PARAM_KEYS)
        if model:
            models.add(model)
        if _is_vector_store_type(node.n8n_type):
            memory_key = node.parameters.get("memoryKey")
            if isinstance(memory_key, str) and memory_key.strip():
                vector_stores.add(memory_key)
            else:
                vector_stores.add(f"{node.n8n_type}:{key}")
        if _is_tool_type(node.n8n_type) or isinstance(node, ToolNode):
            workflow_id = node.parameters.get("workflowId")
            if isinstance(workflow_id, str) and workflow_id.strip():
                tools.add(workflow_id)
            else:
                tools.add(f"{node.n8n_type}:{key}")
        if isinstance(node, TriggerNode) and node.n8n_type in _WEBHOOK_TYPES:
            path = node.parameters.get("path")
            method = node.parameters.get("httpMethod")
            if isinstance(path, str) and path:
                webhooks.append(ResourceRef(
                    id=path,
                    metadata={"method": method, "path": path, "node": key},
                ))

    manifest.models = [ResourceRef(id=value) for value in sorted(models)]
    manifest.vector_stores = [ResourceRef(id=value) for value in sorted(vector_stores)]
    manifest.tools = [ResourceRef(id=value) for value in sorted(tools)]
    manifest.webhooks = sorted(webhooks, key=lambda r: (r.id, str(r.metadata)))
    manifest.credentials = _collect_credentials(ast)
    manifest.bind_status["webhook"] = "declared" if manifest.webhooks else "not_required"
    for resource, items in (
        ("model", manifest.models),
        ("vector_store", manifest.vector_stores),
        ("tool", manifest.tools),
        ("credential", manifest.credentials),
    ):
        manifest.bind_status[resource] = "lazy_deferred" if items else "not_required"
    return manifest
