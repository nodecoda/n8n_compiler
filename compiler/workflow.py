"""强类型 AST -> 可序列化编译 IR — 对齐 coze_compiler.compiler.workflow。

n8n 适配点：
  - 无复合节点/层级：execution_order 单 scope（__root__），hierarchy 恒空
  - entry_keys 列表（n8n 可多 trigger：webhook + errorTrigger 等）
  - exit 为合成 __exit__ 收口（respondToWebhook 等 sink 不接 exit）
  - 节点级 error_policy（N8NErrorPolicy）取代 coze 的 exception_config
  - Code 节点一等公民：js contract + js_ast 序列化进 config.js / config.js_ast
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ast_nodes.node_type import EXIT_NODE_KEY, ROOT_SCOPE, NodeKind
from ast_nodes.nodes import NodeDecl, WorkflowAST
from checker.validator import validate_workflow
from compiler.dependency import NodeDependencies, resolve_all_dependencies
from manifest import DependencyManifest, build_manifest
from typed_ir import IR_FORMAT, IR_VERSION, compute_typed_ir_digest

_TRIGGER_KINDS = {NodeKind.TRIGGER, NodeKind.ERROR_TRIGGER}

# P2-2（v5）：settings 白名单对照 n8n REST workflowSettings.yml
# （additionalProperties:false，未知键 deploy 400）。warning 不阻断编译——
# 未来 n8n 新键保持兼容，仅在 deploy 前给出编译期信号。
_SETTINGS_ALLOWED: frozenset[str] = frozenset({
    "saveExecutionProgress", "saveManualExecutions", "saveDataErrorExecution",
    "saveDataSuccessExecution", "executionTimeout", "errorWorkflow", "timezone",
    "executionOrder", "binaryMode", "callerPolicy", "callerIds", "timeSavedMode",
    "timeSavedPerExecution", "redactionPolicy", "availableInMCP",
    "customTelemetryTags", "credentialResolverId",
})
_SETTINGS_ENUMS: dict[str, frozenset[str]] = {
    "saveDataErrorExecution": frozenset({"all", "none"}),
    "saveDataSuccessExecution": frozenset({"all", "none"}),
    "binaryMode": frozenset({"separate", "combined"}),
    "callerPolicy": frozenset({"any", "none", "workflowsFromAList", "workflowsFromSameOwner"}),
    "timeSavedMode": frozenset({"fixed", "dynamic"}),
    "redactionPolicy": frozenset({"none", "non-manual", "manual-only", "all"}),
}
_SETTINGS_BOOLS = frozenset({"saveExecutionProgress", "saveManualExecutions", "availableInMCP"})
_SETTINGS_NUMBERS = frozenset({"executionTimeout", "timeSavedPerExecution"})


def check_workflow_settings(settings: dict[str, Any]) -> list[str]:
    """settings 对照 n8n REST 契约的 warning（不阻断编译）。"""
    warnings: list[str] = []
    for key, value in settings.items():
        if key not in _SETTINGS_ALLOWED:
            warnings.append(
                f"workflow.settings.{key}: unknown key (n8n REST workflowSettings "
                "schema is additionalProperties:false — deploy may reject with 400)"
            )
        elif key in _SETTINGS_BOOLS and not isinstance(value, bool):
            warnings.append(
                f"workflow.settings.{key}: expected boolean, got {type(value).__name__}")
        elif key in _SETTINGS_NUMBERS and not isinstance(value, (int, float)):
            warnings.append(
                f"workflow.settings.{key}: expected number, got {type(value).__name__}")
        elif key in _SETTINGS_ENUMS and value not in _SETTINGS_ENUMS[key]:
            warnings.append(
                f"workflow.settings.{key}: {value!r} not in {sorted(_SETTINGS_ENUMS[key])}")
    return warnings


def _entry_keys(ast: WorkflowAST) -> list[str]:
    """入口 = trigger 节点（n8n 可多个）。

    P2-1（v4）：注册表未覆盖的 trigger 类型会落 GENERIC 导致入口恒空；
    用「类型名含 trigger」启发式兜底（n8n trigger 节点统一以 *Trigger 命名），
    与注册表双保险。无 trigger 的工作流仍返回 []（无入口是真实语义，
    零入边普通节点不应被硬造为入口——v1 保持显式空）。
    """
    keys = sorted(
        key for key, node in ast.nodes.items()
        if node.node_type in _TRIGGER_KINDS
    )
    if keys:
        return keys
    return sorted(
        key for key, node in ast.nodes.items()
        if "trigger" in node.n8n_type.lower() and key != EXIT_NODE_KEY
    )


def _ai_only_subnodes(ast: WorkflowAST) -> set[str]:
    """AI 子节点（仅经 ai_* 子连接引用、无任何 main 边的节点）。

    n8n 语义：子节点（Chat Model/Embeddings/Tool 等）只经 ai_* 连接挂在
    主节点上，运行时由 agent 拉取，不参与 main 数据流——拓扑序必须排除，
    否则它们以入度 0 误进 ready 集、排在 trigger 之前。
    """
    main_participants = (
        {c.from_node for c in ast.connections}
        | {c.to_node for c in ast.connections}
    )
    # P3-2（v5）：只排除「纯 ai 源点」——ai 边的 to_node（agent/模型等主节点）
    # 即便无 main 边（浮空 AI 主节点）也保留入拓扑序：其入度按 ai 源点排除后
    # 自然为 0，作为工作流入口面被显式保留，不做静默丢弃（与 typed_ir 侧
    # _main_topology_nodes 逐字段等价，漂移由 IR 校验自身闭环兜底）。
    return {c.from_node for c in ast.ai_connections} - main_participants


def _execution_orders(ast: WorkflowAST) -> dict[str, list[str]]:
    """执行顺序：Kahn 拓扑排序（单 scope，ready.sort() 保证确定性）。

    边集合 = main 连接（AI 子连接不参与数据流拓扑）；AI 子节点被排除在
    拓扑序外（见 _ai_only_subnodes），typed_ir 校验同步按 main 拓扑节点
    集合做全排列断言。
    """
    members = set(ast.nodes) - _ai_only_subnodes(ast)
    incoming = dict.fromkeys(members, 0)
    outgoing: dict[str, list[str]] = {node: [] for node in members}
    for connection in ast.connections:
        if connection.from_node in members and connection.to_node in members:
            outgoing[connection.from_node].append(connection.to_node)
            incoming[connection.to_node] += 1

    ready = sorted(node for node, count in incoming.items() if count == 0)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for successor in sorted(outgoing[current]):
            incoming[successor] -= 1
            if incoming[successor] == 0:
                ready.append(successor)
                ready.sort()
    if len(order) != len(members):
        unresolved = sorted(members - set(order))
        raise ValueError(f"workflow contains a cycle: {unresolved}")
    return {ROOT_SCOPE: order}


def _node_to_dict(node: NodeDecl, dependencies: NodeDependencies) -> dict[str, Any]:
    """NodeDecl -> IR node dict。"""
    return {
        "key": node.key,
        "type": node.node_type.value,
        "name": node.name,
        "parent_key": node.parent_key,
        "input_types": {name: info.to_dict() for name, info in node.input_types.items()},
        "output_types": {name: info.to_dict() for name, info in node.output_types.items()},
        "input_sources": [source.to_dict() for source in node.input_sources],
        "output_sources": [source.to_dict() for source in node.output_sources],
        "error_policy": node.error_policy.to_dict(),
        "dependencies": dependencies.to_dict(),
        "config": node.to_config_dict(),
    }


def _serialize_ir(
    ast: WorkflowAST,
    dependencies: dict[str, NodeDependencies],
    manifest: DependencyManifest,
    *,
    workflow_id: str,
    version: str,
) -> CompiledWorkflow:
    """序列化 typed IR 文档。"""
    body: dict[str, Any] = {
        "format": IR_FORMAT,
        "format_version": IR_VERSION,
        "workflow": {
            "id": workflow_id,
            "version": version,
            "entry_keys": _entry_keys(ast),
            "exit_key": EXIT_NODE_KEY,
            # P3-8（v4）：IR v2 携带源 settings 原值——修复「恒 v1 硬编码覆盖」
            # 语义边界（源 v2 工作流不再被强制降级 v1）。缺失 = 源无 settings，
            # decompile 回退编辑器默认 v1（REST schema 要求字段存在）。
            "settings": ast.settings,
        },
        "nodes": [
            _node_to_dict(ast.nodes[key], dependencies[key])
            for key in sorted(ast.nodes)
        ],
        # P1-1c（v4）：main + ai 子连接一并序列化，每条带 conn_type（v2）。
        # ai 边方向（子节点 -> 主节点）与 main 相反，原样记录；decompile
        # 按 conn_type 分组还原回 connections[src][conn_type][端口]。
        "connections": [
            {
                "from_node": connection.from_node,
                "to_node": connection.to_node,
                "from_port": connection.from_port,
                "to_port": connection.to_port,
                "to_index": connection.to_index,
                "conn_type": connection.conn_type,
            }
            for connection in ast.connections + ast.ai_connections
        ],
        "hierarchy": dict(ast.hierarchy),
        "execution_order": _execution_orders(ast),
        "manifest": manifest.to_dict(),
    }
    body["digest"] = compute_typed_ir_digest(body)
    return CompiledWorkflow(body)


@dataclass
class CompiledWorkflow:
    """编译产物：n8n-typed-ir 文档。"""
    document: dict[str, Any]
    # P2-2（v5）：settings 契约 warning（不阻断编译；CLI 打 stderr）
    warnings: list[str] = field(default_factory=list)

    @property
    def digest(self) -> str:
        return self.document["digest"]

    @property
    def manifest(self) -> dict[str, Any]:
        return self.document["manifest"]

    @property
    def nodes(self) -> list[dict[str, Any]]:
        return self.document["nodes"]

    def find_node(self, key: str) -> dict[str, Any]:
        for item in self.document["nodes"]:
            if item["key"] == key:
                return item
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        return self.document

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.document, ensure_ascii=False, indent=indent, sort_keys=True)


def compile_ast(
    ast: WorkflowAST,
    *,
    workflow_id: str = "",
    version: str = "",
) -> CompiledWorkflow:
    """编译：校验 -> 依赖解析 -> manifest -> 序列化 IR。"""
    validate_workflow(ast, raise_on_error=True)
    dependencies = resolve_all_dependencies(ast)
    manifest = build_manifest(ast)
    compiled = _serialize_ir(
        ast, dependencies, manifest,
        workflow_id=workflow_id,
        version=version,
    )
    # P2-2（v5）：settings 契约 warning 附加到产物（CLI 打 stderr，不阻断）
    compiled.warnings = check_workflow_settings(ast.settings)
    return compiled
