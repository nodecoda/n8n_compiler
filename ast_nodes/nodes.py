"""强类型工作流 AST — 对齐 coze_compiler.ast_nodes.nodes。

NodeDecl 是基类，只包含所有节点共享的公共字段。具体节点类型在 node_decls.py
定义，每个子类直接持有自己的专有字段（如 CodeNode.js_ast、HTTPNode 参数），
消费方通过 isinstance(node, CodeNode) 收窄后直接访问专有字段。

n8n 适配：
  - node_type = NodeKind（语义分类，对应 KIND）
  - n8n_type = 原始 type 字符串（"n8n-nodes-base.if"）
  - input_types/output_types 以端口名为键（"main"/"main_0"/"main_1"…）
  - 表达式/字面量统一为 input_sources（FieldInfo 列表）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from type_system.typeinfo import TypeInfo
from values.reference import FieldInfo

from .configs import N8NErrorPolicy
from .connection import Connection
from .node_type import NodeKind

if TYPE_CHECKING:
    from scope.scope import Scope
    from scope.symbol_table import SymbolTable

_MAIN = "main"


@dataclass(kw_only=True)
class NodeDecl:
    """节点声明基类 - 所有节点类型共享的公共字段。"""

    KIND: ClassVar[str] = ""
    key: str
    n8n_type: str
    node_type: NodeKind
    name: str
    type_version: float = 1.0
    position: tuple[float, float] = (0.0, 0.0)
    parameters: dict[str, Any] = field(default_factory=dict)
    input_types: dict[str, TypeInfo] = field(default_factory=dict)
    output_types: dict[str, TypeInfo] = field(default_factory=dict)
    input_sources: list[FieldInfo] = field(default_factory=list)
    output_sources: list[FieldInfo] = field(default_factory=list)
    error_policy: N8NErrorPolicy = field(default_factory=N8NErrorPolicy)
    credentials: dict[str, Any] = field(default_factory=dict)  # raw node.credentials
    parent_key: str | None = None
    scope: Scope | None = None

    @property
    def input_port_count(self) -> int:
        return 0 if not self.input_types else 1  # 覆盖点：多输入节点在子类重写

    def input_type_at(self, path: list[str]) -> TypeInfo | None:
        """沿路径取输入类型（端口名优先，退化到 main）——output_type_at 对称。"""
        if not path:
            return None
        root_types = self.input_types
        current = root_types.get(path[0])
        if current is None and _MAIN in root_types:
            current = root_types[_MAIN]
            rest = path
        else:
            rest = path[1:]
        for part in rest:
            if current is None or not current.is_object():
                return None
            current = current.properties.get(part)
        return current

    def output_type_at(self, path: list[str]) -> TypeInfo | None:
        """沿路径取输出类型（端口名优先，退化到 main）。"""
        if not path:
            return None
        root_types = self.output_types
        current = root_types.get(path[0])
        if current is None and _MAIN in root_types:
            current = root_types[_MAIN]
            rest = path
        else:
            rest = path[1:]
        for part in rest:
            if current is None or not current.is_object():
                return None
            current = current.properties.get(part)
        return current

    def to_config_dict(self) -> dict[str, Any]:
        """节点专有字段序列化为 IR config dict。子类扩展。"""
        return {
            "kind": self.KIND,
            "n8n_type": self.n8n_type,
            "type_version": self.type_version,
            "position": list(self.position),
            "parameters": self.parameters,
            "error_policy": self.error_policy.to_dict(),
            # 凭据引用（n8n workflow JSON 顶层字段，仅 id/name 引用，
            # 不含敏感值）——部署/反编译还原认证绑定必需（架构审核 P2-13）
            "credentials": dict(self.credentials),
        }


@dataclass
class WorkflowAST:
    """工作流 AST：节点 + 连接 + 层级 + 符号表。"""
    nodes: dict[str, NodeDecl] = field(default_factory=dict)
    connections: list[Connection] = field(default_factory=list)
    # P1-1c（v4）：非 main 子连接（ai_languageModel/ai_tool/ai_embedding 等）完整
    # 携带，独立于 main connections——Kahn 拓扑/exit 收口只走 main 边，
    # ai 边方向（子节点 -> 主节点）与 main 相反且由 agent 运行时拉取。
    ai_connections: list[Connection] = field(default_factory=list)
    hierarchy: dict[str, str] = field(default_factory=dict)  # n8n 恒空（无复合节点）
    ai_referenced: set[str] = field(default_factory=set)  # 经 ai_* 子连接引用的节点（不参与 main 拓扑）
    # P1-1a（v4）引入、P1-1c（v4）语义更新：非 main 连接按类型计边数。
    # v1 时是「被丢弃量」；v2 起 IR 完整携带，本字段为携带边数（观测用，
    # manifest.ai_connections_dropped 同步保留字段名、语义同改为携带数）。
    non_main_connections: dict[str, int] = field(default_factory=dict)
    symbol_table: SymbolTable | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    pin_data: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def entry_keys(self) -> list[str]:
        """trigger 节点（无 main 输入）为工作流入口。"""
        return [key for key, node in self.nodes.items()
                if node.node_type == NodeKind.TRIGGER or node.input_port_count == 0]

    def predecessors(self, node_key: str) -> set[str]:
        """node_key 的直接上游节点（main 连线）。"""
        result: set[str] = set()
        for conn in self.connections:
            if conn.to_node == node_key:
                result.add(conn.from_node)
        return result

    def upstream_of(self) -> dict[str, set[str]]:
        """每个节点 -> 直接上游节点集合（主连线）。"""
        result: dict[str, set[str]] = {key: set() for key in self.nodes}
        for conn in self.connections:
            if conn.to_node in result:
                result[conn.to_node].add(conn.from_node)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": {key: node_to_config_dict(node) for key, node in self.nodes.items()},
            "connections": [c.to_dict() for c in self.connections],
            "ai_connections": [c.to_dict() for c in self.ai_connections],
            "hierarchy": dict(self.hierarchy),
        }


def node_to_config_dict(node: NodeDecl) -> dict[str, Any]:
    """NodeDecl -> IR config dict（对齐 coze node_config_to_dict）。"""
    return {
        "key": node.key,
        "type": node.node_type.value,
        "n8n_type": node.n8n_type,
        "name": node.name,
        "parent_key": node.parent_key,
        "input_types": {k: v.to_dict() for k, v in node.input_types.items()},
        "output_types": {k: v.to_dict() for k, v in node.output_types.items()},
        "input_sources": [f.to_dict() for f in node.input_sources],
        "output_sources": [f.to_dict() for f in node.output_sources],
        "config": node.to_config_dict(),
    }
