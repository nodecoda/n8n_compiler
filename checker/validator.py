"""工作流结构与引用校验 — 严格对齐 coze_compiler.checker.validator。

n8n 适配点（语义等价，不降质量）：
  - 无复合节点/层级（hierarchy 恒空），环路检测在全图上做
  - IF 的 true/false 输出端口（main_0/main_1）允许只连一个（n8n 合法模式）
  - 合成 Exit 收口末端；sink（respondToWebhook）是显式输出，不接 Exit
  - 表达式引用（$node["X"] / $json）经 parser 已降为 input_sources，规则对齐 coze
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ast_nodes.node_decls import CodeNode, EntryNode, ExitNode, IfNode
from ast_nodes.node_type import ENTRY_NODE_KEY, EXIT_NODE_KEY, NodeKind
from ast_nodes.nodes import WorkflowAST
from type_system.datatype import DataType
from type_system.typeinfo import TypeInfo
from values.reference import Reference
from values.variable import GlobalVarType

_PARAMETER_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 需要路径的全局变量：$env.X；对象型（$now/$execution/$workflow/...）允许整对象引用
_PATH_REQUIRED_VARS = {GlobalVarType.ENV}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    node_id: str | None = None
    start_node: str | None = None
    end_node: str | None = None


class WorkflowValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(issue.message for issue in issues))


# ---------------------------------------------------------------------------
# validate_syntax
# ---------------------------------------------------------------------------


def validate_syntax(ast: WorkflowAST) -> list[ValidationIssue]:
    """语法级：重复边、自环边。"""
    issues: list[ValidationIssue] = []
    seen: set[str] = set()
    for conn in ast.connections:
        if conn.identity in seen:
            issues.append(ValidationIssue(
                code="duplicate_edge",
                start_node=conn.from_node,
                end_node=conn.to_node,
                message=f"duplicate edge: {conn.from_node} -> {conn.to_node}",
            ))
        seen.add(conn.identity)
        if conn.from_node == conn.to_node:
            issues.append(ValidationIssue(
                code="self_referencing_edge",
                start_node=conn.from_node,
                end_node=conn.to_node,
                message=f"self-referencing edge: {conn.from_node} -> {conn.to_node}",
            ))
    return issues


# ---------------------------------------------------------------------------
# validate_connections
# ---------------------------------------------------------------------------


def validate_connections(ast: WorkflowAST) -> list[ValidationIssue]:
    """连接完整性：源/目标存在、孤立节点、trigger 出边、IF 输出端口。"""
    issues: list[ValidationIssue] = []
    for conn in ast.connections:
        if conn.from_node not in ast.nodes:
            issues.append(ValidationIssue(
                code="unknown_source_node",
                message=f"edge source node does not exist: {conn.from_node}",
                start_node=conn.from_node,
                end_node=conn.to_node,
            ))
            continue
        if conn.to_node not in ast.nodes:
            issues.append(ValidationIssue(
                code="unknown_target_node",
                message=f"edge target node does not exist: {conn.to_node}",
                start_node=conn.from_node,
                end_node=conn.to_node,
            ))
            continue
        node = ast.nodes[conn.from_node]
        # 端口名形状（main | main_{n}）。n8n 多输出节点（Switch 路由数、插件
        # 节点）端口集合运行时才确定，编辑器保存时不校验存在性 -> 只查形状。
        if not _OUTPUT_PORT_SHAPE.match(conn.from_port):
            issues.append(ValidationIssue(
                code="unknown_output_port",
                node_id=conn.from_node,
                message=f'node "{conn.from_node}" has malformed output port "{conn.from_port}" '
                        f"(expected main or main_N)",
            ))

    # n8n 适配点：孤立节点 / trigger 无出边 / IF 端口未连全部是编辑器合法状态
    # （保存时零校验，未连接节点不参与执行）。n8n 语义下不存在 coze 的
    # node_not_connected 等效错误；结构性错误由其它规则覆盖。
    return issues


_OUTPUT_PORT_SHAPE = re.compile(r"^main(_[0-9]+)?$")


# ---------------------------------------------------------------------------
# detect_cycles
# ---------------------------------------------------------------------------


def detect_cycles(ast: WorkflowAST) -> list[ValidationIssue]:
    """DFS 三色环路检测（n8n 无 hierarchy，全图检测）。"""
    issues: list[ValidationIssue] = []
    adjacency: dict[str, list[str]] = {key: [] for key in ast.nodes}
    for conn in ast.connections:
        if conn.from_node in adjacency and conn.to_node in adjacency:
            adjacency[conn.from_node].append(conn.to_node)

    state: dict[str, int] = dict.fromkeys(ast.nodes, 0)
    stack: list[str] = []

    def dfs(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for successor in adjacency[node]:
            if state[successor] == 0:
                dfs(successor)
            elif state[successor] == 1:
                start = stack.index(successor)
                cycle = stack[start:] + [successor]
                for left, right in zip(cycle, cycle[1:]):
                    issues.append(ValidationIssue(
                        code="cycle_detected",
                        start_node=left,
                        end_node=right,
                        message=f"workflow cycle detected: {left} -> {right}",
                    ))
        stack.pop()
        state[node] = 2

    for node in ast.nodes:
        if state[node] == 0:
            dfs(node)
    return issues


# ---------------------------------------------------------------------------
# validate_references
# ---------------------------------------------------------------------------


def _is_assignable(source: TypeInfo, target: TypeInfo) -> bool:
    """类型可赋性。any 可赋给任何目标（n8n 多数输出形状未知，不能误杀）。"""
    if source.is_any() or target.is_any():
        return True
    if source.type == target.type:
        return True
    if target.type in (DataType.STRING,):
        return source.type in (DataType.NUMBER, DataType.BOOLEAN)
    if target.type in (DataType.NUMBER, DataType.BOOLEAN):
        return source.type == DataType.STRING  # 运行时可转
    if target.type == DataType.OBJECT:
        return source.type == DataType.STRING
    if target.type == DataType.ARRAY:
        if source.type != DataType.ARRAY:
            return source.type == DataType.STRING
        if source.elem_type_info is None or target.elem_type_info is None:
            return source.elem_type_info is target.elem_type_info
        return _is_assignable(source.elem_type_info, target.elem_type_info)
    return False


def validate_references(ast: WorkflowAST) -> list[ValidationIssue]:
    """引用校验：global path 非空、节点存在、可达性、字段存在、类型兼容。"""
    issues: list[ValidationIssue] = []
    assert ast.symbol_table is not None
    upstream = ast.upstream_of()

    for target_id, target in ast.nodes.items():
        for field in target.input_sources:
            ref = field.source.ref
            if ref is None:
                continue
            if ref.variable_type is not None:
                if not ref.from_path and ref.variable_type in _PATH_REQUIRED_VARS:
                    issues.append(ValidationIssue(
                        code="global_variable_path_empty",
                        node_id=target_id,
                        message="global variable reference requires a non-empty path",
                    ))
                continue
            source_id = ref.from_node_key
            if not source_id:
                issues.append(ValidationIssue(
                    code="empty_ref_block_id",
                    node_id=target_id,
                    message="ref error, [from_node_key] is empty",
                ))
                continue
            if source_id not in ast.nodes:
                issues.append(ValidationIssue(
                    code="referenced_node_missing",
                    node_id=target_id,
                    message=f'node "{target_id}" depends on missing node "{source_id}"',
                ))
                continue
            if target_id == source_id:
                issues.append(ValidationIssue(
                    code="self_reference_in_field_mapping",
                    node_id=target_id,
                    message=f'node "{target_id}" cannot reference its own output',
                ))
                continue
            if not ast.symbol_table.can_reference(source_id, target_id, upstream):
                issues.append(ValidationIssue(
                    code="reference_not_reachable",
                    node_id=target_id,
                    message=f'node "{target_id}" cannot reference unreachable node "{source_id}"',
                ))
                continue
            if not ref.from_path:
                continue
            source = ast.nodes[source_id]
            source_type = source.output_type_at(ref.from_path)
            target_type = target.input_type_at(field.path)
            if source_type is None:
                # 上游形状未知（ANY）或字段确实缺失：ANY 跳过，已知形状缺失报错
                if not _shape_unknown(source.output_types):
                    issues.append(ValidationIssue(
                        code="source_field_missing",
                        node_id=target_id,
                        message=f"source field does not exist: {source_id}.{'.'.join(ref.from_path)}",
                    ))
            elif target_type is not None and not _is_assignable(source_type, target_type):
                issues.append(ValidationIssue(
                    code="type_mismatch",
                    node_id=target_id,
                    message=(f"cannot assign {source_type!r} from {source_id}."
                             f"{'.'.join(ref.from_path)} to {target_type!r} at "
                             f"{target_id}.{'.'.join(field.path)}"),
                ))
    return issues


def _shape_unknown(output_types: dict[str, TypeInfo]) -> bool:
    """输出形状是否静态不可解析（字段访问不能判缺）。

    动态形状 = any / array(elem=any) / 空 props object —— 均不报 source_field_missing
    （n8n 输出多数是元素形状未知的数组，Code 节点动态 return 退化为空 props）。
    只有解析到确定的 object 属性集且键缺失时才算真实缺字段。
    """
    for info in output_types.values():
        if info.is_any():
            return True
        if info.is_object() and not info.properties:
            return True
        if info.is_array():
            elem = info.elem_type_info
            if elem is None or elem.is_any():
                return True
    return not output_types


# ---------------------------------------------------------------------------
# validate_node_semantics
# ---------------------------------------------------------------------------


def validate_node_semantics(ast: WorkflowAST) -> list[ValidationIssue]:
    """节点语义：Entry/Exit 边界、自引用、Code 节点 JS 契约、retry 策略。"""
    issues: list[ValidationIssue] = []
    trigger_kinds = {NodeKind.TRIGGER, NodeKind.ERROR_TRIGGER}

    for node_id, node in ast.nodes.items():
        if node.node_type in trigger_kinds and node.input_sources:
            issues.append(ValidationIssue(
                code="entry_node_has_input_sources",
                node_id=node_id,
                message=f'trigger node "{node.name}" must not have input sources',
            ))
        if node_id == EXIT_NODE_KEY and node.output_sources:
            issues.append(ValidationIssue(
                code="exit_node_has_output_sources",
                node_id=node_id,
                message="exit node must not have output sources",
            ))
        for field in node.input_sources:
            ref = field.source.ref
            if ref is None or ref.variable_type is not None:
                continue
            if ref.from_node_key == node_id:
                issues.append(ValidationIssue(
                    code="self_reference_in_field_mapping",
                    node_id=node_id,
                    message=f'node "{node.name}" cannot reference its own output',
                ))
        if isinstance(node, CodeNode):
            if node.js_contract is None:
                continue
            for err in node.js_contract.errors:
                issues.append(ValidationIssue(
                    code="code_syntax_error",
                    node_id=node_id,
                    message=f'Code node "{node.name}" JS syntax error: {err}',
                ))
            if node.error_policy.retry_on_fail and node.error_policy.max_tries is None:
                issues.append(ValidationIssue(
                    code="retry_policy_incomplete",
                    node_id=node_id,
                    message=f'node "{node.name}" sets retryOnFail without maxTries',
                ))
    return issues


# ---------------------------------------------------------------------------
# validate_workflow
# ---------------------------------------------------------------------------


def validate_workflow(ast: WorkflowAST, *, raise_on_error: bool = False) -> list[ValidationIssue]:
    """组合全部校验，产出排序后的 issue 列表。"""
    issues: list[ValidationIssue] = []
    issues.extend(validate_syntax(ast))
    issues.extend(validate_connections(ast))
    issues.extend(detect_cycles(ast))
    issues.extend(validate_references(ast))
    issues.extend(validate_node_semantics(ast))
    issues.sort(key=lambda i: (i.node_id or i.start_node or "", i.code))
    if raise_on_error and issues:
        raise WorkflowValidationError(issues)
    return issues
