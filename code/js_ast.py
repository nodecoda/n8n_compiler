"""ESTree AST analysis over the acorn parse output.

Runs on the JSON-decoded acorn AST (dicts) and extracts the static contract:
  - input dependencies (items[0].json.<path>, $json.<path>, item.json.<path>,
    $input, $node["X"], $("X"), $items("X") — n8n built-in call forms)
  - output shape (return literal object -> typed props; items/map -> list; none -> void)
  - side-effect class (fetch/require/fs/timers/Date/Math.random -> IO; else PURE)
  - compile-time rejections: import/export/dynamic import() are n8n-runtime
    errors made into compile errors (n8n Code nodes cannot load modules, #9464)
  - warnings: network/fs calls (sandbox will fail at runtime), dynamic
    subscripts that silently drop dependency tracking

Only the OUTERMOST MemberExpression of each chain is recorded as a dependency
(items[0].json.user.address.city -> one dep), and the output shape comes from
the LAST top-level return (bare script or `export default function main`).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .contract import (
    CodeEffect,
    FieldDep,
    OutputShape,
    OutputShapeKind,
)

_DEPS_BASES = ("items", "item", "$json", "$input", "$node")
# $("X") / $items("X") / $item("X", i): n8n built-in node-reference call forms
_NODE_REF_CALLS = {"$", "$items", "$item"}
# $node["X"].json.y -> base="$node", path=("X", "y")（首个元素是目标节点名）
_IO_CALLS = {
    "fetch", "require", "XMLHttpRequest", "WebSocket", "Worker",
    "setTimeout", "setInterval", "open",
}
_IO_MEMBER_ROOTS = {
    "fs", "http", "https", "process", "child_process", "crypto", "console",
}
_NON_DETERMINISTIC = {"Date", "Math"}
# 沙盒内运行时必然失败（或受配置门控）的调用 -> 编译期 warning
_NETWORK_CALLS = {"fetch", "axios", "XMLHttpRequest", "WebSocket"}
_FS_MEMBER_ROOTS = {"fs", "process", "child_process"}
_REQUIRE_NAME = "require"


def _iter_child_nodes(node: Dict[str, Any]):
    for value in node.values():
        if isinstance(value, dict) and "type" in value:
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and "type" in item:
                    yield item


def _walk(node: Dict[str, Any], parent: Optional[Dict[str, Any]] = None):
    """Depth-first walk yielding (node, parent)."""
    yield node, parent
    for child in _iter_child_nodes(node):
        yield from _walk(child, node)


def _pos(node: Dict[str, Any]) -> str:
    start = node.get("start")
    return f"{start}" if isinstance(start, int) else "?"


# ---------------------------------------------------------------------------
# input dependencies
# ---------------------------------------------------------------------------


def _member_chain(node: Dict[str, Any]) -> Optional[Tuple[str, List[str]]]:
    """Resolve a MemberExpression chain rooted at a dependency base.

    Roots: dependency-base identifiers (items/$json/...) or built-in node-ref
    calls ($("X"), $items("X"), $item("X", i)). Returns None when the chain
    cannot be traced statically (dynamic subscript); callers should surface a
    warning via collect_warnings instead of silently dropping the dependency.
    """
    parts: List[str] = []
    cur = node
    while cur.get("type") == "MemberExpression":
        prop = cur.get("property", {})
        computed = cur.get("computed", False)
        if computed and prop.get("type") == "Literal":
            value = prop.get("value")
            if isinstance(value, str):
                parts.insert(0, value)          # ["key"] -> field path
            elif isinstance(value, (int, float)):
                pass                             # items[0] -> index, not a field
            else:
                return None                      # dynamic index — cannot trace
        elif not computed and prop.get("type") == "Identifier":
            parts.insert(0, prop.get("name", ""))
        else:
            return None
        cur = cur.get("object", {})
        if cur.get("type") == "CallExpression":
            break
    if cur.get("type") == "CallExpression":
        # $("X") / $items("X") / $item("X", i) 根：引用节点输出
        callee = cur.get("callee", {})
        name = callee.get("name") if callee.get("type") == "Identifier" else None
        if name not in _NODE_REF_CALLS:
            return None
        args = cur.get("arguments", [])
        if not args or args[0].get("type") != "Literal" or not isinstance(args[0].get("value"), str):
            return None
        parts.insert(0, args[0]["value"])
        base = "$node"
    elif cur.get("type") != "Identifier" or cur.get("name") not in _DEPS_BASES:
        return None
    else:
        base = cur["name"]

    if base in ("items", "item") and parts and parts[0] == "json":
        parts.pop(0)
    if base == "$node" and len(parts) >= 2 and parts[1] == "json":
        parts.pop(1)
    return base, parts


def extract_deps(ast: Dict[str, Any]) -> List[FieldDep]:
    deps: List[FieldDep] = []
    seen = set()
    for node, parent in _walk(ast):
        if node.get("type") == "CallExpression":
            # $("X") / $items("X") 直接引用（非 member chain 根）
            callee = node.get("callee", {})
            if callee.get("type") == "Identifier" and callee.get("name") in _NODE_REF_CALLS:
                args = node.get("arguments", [])
                if args and args[0].get("type") == "Literal" and isinstance(args[0].get("value"), str):
                    key = ("$node", (args[0]["value"],))
                    if key not in seen:
                        seen.add(key)
                        deps.append(FieldDep(base="$node", path=(args[0]["value"],)))
            continue
        if node.get("type") != "MemberExpression":
            continue
        # method call boundary: items.map(...) reads nothing
        if parent is not None and parent.get("type") == "CallExpression" and parent.get("callee") is node:
            continue
        # inner levels of a longer chain are subsumed by the outermost node
        if parent is not None and parent.get("type") == "MemberExpression" and parent.get("object") is node:
            continue
        chain = _member_chain(node)
        if chain is None:
            continue
        base, path = chain
        key = (base, tuple(path))
        if key in seen:
            continue
        seen.add(key)
        deps.append(FieldDep(base=base, path=tuple(path)))
    return deps


# ---------------------------------------------------------------------------
# compile-time rejections (n8n runtime errors made into compile errors)
# ---------------------------------------------------------------------------


def reject_module_syntax(ast: Dict[str, Any]) -> List[str]:
    """import/export/动态 import() -> 编译错误（n8n Code 节点运行时不可用）。

    依据：n8n task-runner 的 VmCodeWrapper 是函数包装的裸脚本，无模块图；
    官方 Common Issues + issue #9464 确认 import/export/动态 import() 均不可用。
    """
    errors: List[str] = []
    for node, _parent in _walk(ast):
        t = node.get("type")
        if t in ("ImportDeclaration", "ExportNamedDeclaration", "ExportDefaultDeclaration", "ExportAllDeclaration"):
            errors.append(f"{_pos(node)}: {t} is not supported in n8n Code nodes (no module loader; use require() with allowlist)")
        elif t == "ImportExpression":
            errors.append(f"{_pos(node)}: dynamic import() is not supported in n8n Code nodes (issue #9464)")
    return errors


# ---------------------------------------------------------------------------
# warnings (runtime-failure risks / lost precision)
# ---------------------------------------------------------------------------


def collect_warnings(ast: Dict[str, Any]) -> List[str]:
    """编译期 warning：网络/文件系统调用、require 门控、动态下标丢依赖。"""
    warnings: List[str] = []
    for node, _parent in _walk(ast):
        t = node.get("type")
        if t == "CallExpression":
            callee = node.get("callee", {})
            if callee.get("type") == "Identifier":
                name = callee.get("name")
                if name in _NETWORK_CALLS:
                    warnings.append(f"{_pos(node)}: {name}() is unavailable in the n8n sandbox (network is blocked at runtime)")
                elif name == _REQUIRE_NAME:
                    warnings.append(f"{_pos(node)}: require() is gated by NODE_FUNCTION_ALLOW_BUILTIN/EXTERNAL (default: all modules blocked)")
            elif callee.get("type") == "MemberExpression":
                obj = callee.get("object", {})
                if obj.get("type") == "Identifier" and obj.get("name") in _FS_MEMBER_ROOTS:
                    warnings.append(f"{_pos(node)}: {obj.get('name')}.* is unavailable in the n8n sandbox (fs/process blocked at runtime)")
        if t == "MemberExpression" and node.get("computed"):
            prop = node.get("property", {})
            if prop.get("type") != "Literal":
                warnings.append(
                    f"{_pos(node)}: dynamic subscript cannot be traced statically; "
                    "dependency on this field is not recorded"
                )
    return warnings


# ---------------------------------------------------------------------------
# output shape
# ---------------------------------------------------------------------------


def _literal_type(value: Any) -> str:
    if value is None:
        return "any"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "any"


def _expr_type(node: Dict[str, Any]) -> str:
    t = node.get("type")
    if t == "Literal":
        return _literal_type(node.get("value"))
    if t == "TemplateLiteral":
        return "string"
    if t == "ArrayExpression":
        return "array"
    if t == "ObjectExpression":
        return "object"
    if t == "Identifier":
        return "any"
    if t == "UnaryExpression":
        return "boolean" if node.get("operator") == "!" else "any"
    return "any"


def _object_props(node: Dict[str, Any]) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for prop in node.get("properties", []):
        if prop.get("type") != "Property":
            continue
        key = prop.get("key", {})
        if key.get("type") == "Identifier":
            name = key.get("name", "")
        elif key.get("type") == "Literal":
            name = str(key.get("value", ""))
        else:
            continue
        props[name] = _expr_type(prop.get("value", {}))
    return props


def _build_parent_map(root: Dict[str, Any]) -> Dict[int, Optional[Dict[str, Any]]]:
    parents: Dict[int, Optional[Dict[str, Any]]] = {id(root): None}

    def _go(node, parent):
        parents[id(node)] = parent
        for child in _iter_child_nodes(node):
            _go(child, node)

    _go(root, None)
    return parents


def _is_top_level_return(ret: Dict[str, Any], parents: Dict[int, Optional[Dict[str, Any]]]) -> bool:
    """True when the return belongs to the program-level function:
    chain is ReturnStatement -> BlockStatement -> FunctionDeclaration
    (child of Program / ExportDefaultDeclaration), or directly in Program.body.
    Crossing an ArrowFunctionExpression / FunctionExpression means it is a
    nested helper's return.
    """
    node = ret
    while True:
        parent = parents.get(id(node))
        if parent is None:
            return False
        pt = parent.get("type")
        if pt == "Program":
            return True
        if pt == "BlockStatement":
            node = parent
            continue
        if pt == "FunctionDeclaration":
            gp = parents.get(id(parent))
            if gp is None or gp.get("type") in ("Program", "ExportDefaultDeclaration"):
                return True
            return False
        if pt in ("FunctionExpression", "ArrowFunctionExpression"):
            return False
        node = parent


def extract_output_shape(ast: Dict[str, Any]) -> OutputShape:
    """Shape from the LAST top-level return statement."""
    parents = _build_parent_map(ast)
    returns = [n for n, _p in _walk(ast) if n.get("type") == "ReturnStatement"]
    if not returns:
        return OutputShape(kind=OutputShapeKind.VOID)
    top_level = [r for r in returns if _is_top_level_return(r, parents)]
    chosen = max(top_level or returns, key=lambda r: r.get("start", 0))
    arg = chosen.get("argument")
    if arg is None:
        return OutputShape(kind=OutputShapeKind.VOID)
    t = arg.get("type")
    if t == "ObjectExpression":
        return OutputShape(kind=OutputShapeKind.OBJECT, props=_object_props(arg))
    if t == "Identifier":
        if arg.get("name") == "items":
            return OutputShape(kind=OutputShapeKind.LIST, elem="any")
        return OutputShape(kind=OutputShapeKind.OBJECT)
    if t == "CallExpression":
        callee = arg.get("callee", {})
        if callee.get("type") == "MemberExpression" and callee.get("object", {}).get("name") == "items":
            return OutputShape(kind=OutputShapeKind.LIST, elem="any")
        return OutputShape(kind=OutputShapeKind.ANY)
    if t == "MemberExpression":
        return OutputShape(kind=OutputShapeKind.OBJECT)
    if t == "ArrayExpression":
        return OutputShape(kind=OutputShapeKind.LIST, elem="any")
    if t == "NewExpression":
        # P1-2（v4）：return new X()（AI 链 supplyData 工厂）——JS 语义保证
        # new 表达式的值恒为对象（构造函数的返回对象或实例本身），如实分类
        # OBJECT（props 静态未知）；比 ANY 更精确且不猜测属性。
        return OutputShape(kind=OutputShapeKind.OBJECT)
    if t in ("Literal", "TemplateLiteral", "UnaryExpression"):
        return OutputShape(kind=OutputShapeKind.OBJECT)
    return OutputShape(kind=OutputShapeKind.ANY)


# ---------------------------------------------------------------------------
# side-effect classification
# ---------------------------------------------------------------------------


def classify_effect(ast: Dict[str, Any]) -> CodeEffect:
    for node, _parent in _walk(ast):
        t = node.get("type")
        if t == "CallExpression":
            callee = node.get("callee", {})
            if callee.get("type") == "Identifier" and callee.get("name") in _IO_CALLS:
                return CodeEffect.IO
            if callee.get("type") == "MemberExpression":
                obj = callee.get("object", {})
                if obj.get("type") == "Identifier" and obj.get("name") in _IO_MEMBER_ROOTS:
                    return CodeEffect.IO
        if t == "NewExpression":
            callee = node.get("callee", {})
            if callee.get("type") == "Identifier" and callee.get("name") in ("Date", "XMLHttpRequest", "WebSocket", "Worker"):
                return CodeEffect.IO
    for node, _parent in _walk(ast):
        if node.get("type") == "MemberExpression":
            obj = node.get("object", {})
            if obj.get("type") == "Identifier" and obj.get("name") in _NON_DETERMINISTIC:
                return CodeEffect.IO
    return CodeEffect.PURE
