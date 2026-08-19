"""字段依赖解析 — 对齐 coze_compiler.compiler.dependency。

n8n 适配点：无复合节点/层级，parent 桶恒空（保留 schema 位以对齐 coze IR 形状）。
direct = main 数据流直接上游的引用；indirect = $node["X"] 跨节点引用；variables =
全局变量（$env/$execution/$workflow/…）绑定；static_values = 字面量。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ast_nodes.nodes import NodeDecl, WorkflowAST
from values.variable import GlobalVarType


@dataclass(frozen=True)
class FieldMapping:
    """字段映射：来源路径 -> 目标路径。"""
    from_path: tuple[str, ...]
    to_path: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"from_path": list(self.from_path), "to_path": list(self.to_path)}


@dataclass(frozen=True)
class StaticValue:
    """静态字面量。"""
    path: tuple[str, ...]
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": list(self.path), "value": self.value}


@dataclass(frozen=True)
class VariableBinding:
    """全局变量绑定。"""
    variable_type: GlobalVarType
    from_path: tuple[str, ...]
    to_path: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "variable_type": self.variable_type.value,
            "from_path": list(self.from_path),
            "to_path": list(self.to_path),
        }


@dataclass
class NodeDependencies:
    """节点依赖（coze NodeDependencies 形状，parent 恒空）。"""
    direct: dict[str, list[FieldMapping]] = field(default_factory=dict)
    indirect: dict[str, list[FieldMapping]] = field(default_factory=dict)
    parent: dict[str, list[FieldMapping]] = field(default_factory=dict)
    static_values: list[StaticValue] = field(default_factory=list)
    variables: list[VariableBinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def serialize(mappings: dict[str, list[FieldMapping]]) -> dict[str, Any]:
            return {
                node: [mapping.to_dict() for mapping in values]
                for node, values in mappings.items()
            }
        return {
            "direct": serialize(self.direct),
            "indirect": serialize(self.indirect),
            "parent": serialize(self.parent),
            "static_values": [value.to_dict() for value in self.static_values],
            "variables": [binding.to_dict() for binding in self.variables],
        }


def resolve_node_dependencies(ast: WorkflowAST, node: NodeDecl) -> NodeDependencies:
    """解析单个节点的依赖（input_sources -> 依赖分类）。"""
    result = NodeDependencies()
    direct_predecessors = ast.predecessors(node.key)

    for field_src in node.input_sources:
        source = field_src.source
        if source.ref is None:
            # P3-6（v4）：n8n 适配器死路径——parser 只为 ref 建立 input_sources
            # （_bind_expr_ref/_bind_code_node），literal-only FieldInfo 从不产生，
            # 故 static_values 恒空。保留为 coze_compiler 对齐残留：coze 语义
            # 需要静态字面量依赖清单，n8n 无需；勿扩展为"字面量也进依赖"。
            result.static_values.append(StaticValue(
                path=tuple(field_src.path),
                value=source.literal,
            ))
            continue

        reference = source.ref
        if reference.variable_type is not None:
            result.variables.append(VariableBinding(
                variable_type=reference.variable_type,
                from_path=tuple(reference.from_path),
                to_path=tuple(field_src.path),
            ))
            continue
        if reference.from_node_key == node.key:
            raise ValueError(
                f"node {node.key} cannot refer to itself: "
                f"{reference.from_path} -> {field_src.path}"
            )

        mapping = FieldMapping(
            from_path=tuple(reference.from_path),
            to_path=tuple(field_src.path),
        )
        source_key = reference.from_node_key
        bucket = result.direct if source_key in direct_predecessors else result.indirect
        bucket.setdefault(source_key, []).append(mapping)
    return result


def resolve_all_dependencies(ast: WorkflowAST) -> dict[str, NodeDependencies]:
    """解析全部节点依赖。"""
    return {
        node_key: resolve_node_dependencies(ast, node)
        for node_key, node in ast.nodes.items()
    }
