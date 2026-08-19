"""n8n 表达式解析 — 参数值 "={{ ... }}" 降为编译期引用。

n8n 表达式（@n8n/expression-runtime 语法）：
  ={{ $json.a.b }}           当前节点输入数据字段（绑定入边上游）
  ={{ $input.all()[0].json.x }} 输入别名（同上）
  ={{ $node["Name"].json.x }}  其他节点输出
  ={{ $env.X }} / $execution.id / $workflow.id / $now / $parameters.x
  ={{ 任意表达式 }}            动态表达式（v1 不深析，标记 UNKNOWN 保留原串）

解析只提取"引用意图"（ParsedRef），最终 Reference 的 from_node_key 绑定
在 parser/workflow.py 阶段完成（$json/$input 需要知道入边上游）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from values.variable import GlobalVarType

_EXPR_RE = re.compile(r"^\s*=\{\{\s*(.*?)\s*\}\}\s*$", re.DOTALL)
_EMBEDDED_RE = re.compile(r"\{\{\s*(.*?)\s*\}\}", re.DOTALL)

_GLOBAL_ROOTS = {
    "$env": GlobalVarType.ENV,
    "$execution": GlobalVarType.EXECUTION,
    "$workflow": GlobalVarType.WORKFLOW,
    "$now": GlobalVarType.NOW,
    "$today": GlobalVarType.NOW,
    "$parameters": GlobalVarType.PARAMETERS,
    "$items": GlobalVarType.ITEMS,
}


class ExprKind(str, Enum):
    NODE = "node"          # $node["X"].json.path
    INPUT = "input"        # $json.path / $input... -> 入边上游
    GLOBAL = "global"      # $env / $execution / $workflow / $now
    UNKNOWN = "unknown"    # 复杂表达式，v1 保留原串


@dataclass(frozen=True)
class ParsedRef:
    kind: ExprKind
    node: str = ""
    path: tuple[str, ...] = ()
    var_type: Optional[GlobalVarType] = None
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind.value, "raw": self.raw}
        if self.node:
            d["node"] = self.node
        if self.path:
            d["path"] = list(self.path)
        if self.var_type is not None:
            d["var_type"] = self.var_type.value
        return d


def is_expression(value: Any) -> bool:
    """值是否为完整 ={{ ... }} 表达式（整串）。内嵌 {{ }} 模板由 parse_value 处理。"""
    if not isinstance(value, str):
        return False
    return _EXPR_RE.match(value) is not None


def _split_path(member: str) -> tuple[str, ...]:
    """'a.b.c' -> ('a','b','c')；兼容 ['a']['b'] 与 .a.b 混写。"""
    parts: list[str] = []
    for chunk in re.split(r"[.\[]", member):
        chunk = chunk.strip().rstrip("]").strip("'\"")
        if chunk:
            parts.append(chunk)
    return tuple(parts)


def _parse_node_ref(body: str) -> Optional[ParsedRef]:
    """$node["X"]... / $node.Name... 形态（n8n 两种合法写法）。"""
    m = re.match(r"\$node\s*\[\s*(['\"])(.*?)\1\s*\]", body)
    if m:
        node = m.group(2)
    else:
        m = re.match(r"\$node\s*\.\s*([A-Za-z_$][\w$]*)", body)
        if not m:
            return None
        node = m.group(1)
    rest = body[m.end():]
    if not rest:
        # $node["X"]：引用整个节点输出（json 对象）
        return ParsedRef(kind=ExprKind.NODE, node=node, path=(), raw=body)
    # 数据访问器：$node["X"].json.a.b | $node["X"].output.a.b
    # （n8n 语义：.json/.binary 是数据访问器，.params/.isExecuted 是节点访问器，
    #  其他属性（如 .body）不是数据路径，运行时求值失败 -> 不绑定，标 UNKNOWN）
    m2 = re.match(r"\.\s*(?:json|output)\s*(?:\.\s*(.*))?$", rest)
    if m2:
        return ParsedRef(
            kind=ExprKind.NODE, node=node,
            path=_split_path(m2.group(1)) if m2.group(1) else (),
            raw=body,
        )
    return None


def _parse_global_ref(body: str) -> Optional[ParsedRef]:
    """$env.X / $execution.id / $now 等。"""
    for root, var_type in _GLOBAL_ROOTS.items():
        if body == root:
            return ParsedRef(kind=ExprKind.GLOBAL, var_type=var_type, path=(), raw=body)
        if body.startswith(root + "."):
            rest = body[len(root) + 1:]
            return ParsedRef(kind=ExprKind.GLOBAL, var_type=var_type, path=_split_path(rest), raw=body)
        if body.startswith(root + "["):
            m = re.match(re.escape(root) + r"\s*\[\s*(['\"])(.*?)\1\s*\]", body)
            if m:
                rest = body[m.end():]
                path = _split_path(m.group(2) + rest)
                return ParsedRef(kind=ExprKind.GLOBAL, var_type=var_type, path=path, raw=body)
    return None


_PURE_PATH_RE = re.compile(
    r"^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*|\[\s*(['\"])\w+\1\s*\])*$"
)


def _parse_input_ref(body: str) -> Optional[ParsedRef]:
    """$json.a.b / $input 形态。

    $json 后的剩余必须是一段纯字段路径（点/下标），否则（运算符、调用、
    拼接等复杂表达式）拒绝 -> 上层标 UNKNOWN，防止部分匹配错误绑定。
    """
    if body == "$json":
        return ParsedRef(kind=ExprKind.INPUT, path=(), raw=body)
    if body.startswith("$json."):
        rest = body[len("$json."):]
        if not _PURE_PATH_RE.match(rest):
            return None
        return ParsedRef(kind=ExprKind.INPUT, path=_split_path(rest), raw=body)
    if body == "$input":
        return ParsedRef(kind=ExprKind.INPUT, path=(), raw=body)
    if body.startswith("$input."):
        # 合法访问器：all()/first()（可选 [n]）/ item，后可跟 .json.path
        # （n8n InputValue 的 item 指向当前 item，与 $json 同源）。
        # 未知方法（$input.custom()）不部分匹配 -> 标 UNKNOWN。
        m = re.match(
            r"\$input\.(?:(?:all|first)\(\)(?:\s*\[\s*\d+\s*\])?|item)"
            r"(?:\s*\.\s*json)?(?:\s*\.\s*(.*))?$",
            body,
        )
        if not m:
            return None
        rest = m.group(1) if m.group(1) else ""
        return ParsedRef(kind=ExprKind.INPUT, path=_split_path(rest), raw=body)
    return None


def parse_expression(value: str) -> ParsedRef:
    """解析完整 ={{ ... }} 表达式为 ParsedRef；复杂表达式 -> UNKNOWN。"""
    m = _EXPR_RE.match(value)
    if not m:
        return ParsedRef(kind=ExprKind.UNKNOWN, raw=value)
    body = m.group(1).strip()
    ref = _parse_node_ref(body)
    if ref is not None:
        return ref
    ref = _parse_global_ref(body)
    if ref is not None:
        return ref
    ref = _parse_input_ref(body)
    if ref is not None:
        return ref
    return ParsedRef(kind=ExprKind.UNKNOWN, raw=body)


def parse_value(value: Any) -> tuple[ParsedRef | None, bool]:
    """参数值 -> (ParsedRef | None, is_dynamic)。

    - 非表达式字符串 / 非字符串 / 数字 / bool / 嵌套字面量 -> (None, False)
    - 完整 ={{ ... }} -> (ParsedRef, True)
    - 含内嵌 {{ }} 的模板串 -> 尽力提取首个引用，is_dynamic=True
    """
    if not isinstance(value, str):
        return None, False
    if is_expression(value):
        return parse_expression(value), True
    if "{{" in value and "}}" in value:
        # 模板串：提取首个内嵌引用意图（其余部分按字面量处理）
        m = _EMBEDDED_RE.search(value)
        if m:
            inner = "={{ " + m.group(1) + " }}"
            return parse_expression(inner), True
        return None, True
    return None, False
