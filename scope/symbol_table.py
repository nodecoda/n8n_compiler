"""符号表 + 可达性 — 对齐 coze_compiler.scope.symbol_table。

n8n 语义：$node["X"] 引用在编辑器里不限制可达性（任意已存在节点均可引用），
但 checker 的 reference_not_reachable 规则需要"数据流可达"判定：
target 能引用 source 当且仅当 source 在 target 的上游闭包中（按 main 连线）。
入口（trigger）节点可被任何节点引用（数据来自工作流输入）。
"""
from __future__ import annotations

from typing import Iterable

from .scope import Scope, ScopeLevel
from .symbol import Symbol, SymbolKind


class SymbolTable:
    def __init__(self, scopes: Iterable[Scope] | None = None) -> None:
        self.scopes: dict[str, Scope] = {}
        for scope in scopes or ():
            self.register_scope(scope)

    def register_scope(self, scope: Scope) -> None:
        self.scopes[scope.name] = scope

    def define(self, scope_name: str, symbol: Symbol, *, replace: bool = False) -> None:
        scope = self.scopes[scope_name]
        scope.define(symbol, replace=replace)

    def resolve(self, scope_name: str, name: str) -> Symbol | None:
        scope = self.scopes.get(scope_name)
        return scope.resolve(name) if scope else None

    def can_reference(self, source_id: str, target_id: str, upstream: dict[str, set[str]]) -> bool:
        """source 是否可被 target 引用（source 在 target 上游闭包中）。

        ``upstream``: 每个节点 -> 直接上游节点集合（主连线）。
        入口节点（无上游）视为工作流输入源，任何节点均可引用。
        """
        if source_id == target_id:
            return False
        if not upstream.get(target_id):
            return False
        # BFS 反向：从 target 向上游搜索 source
        visited: set[str] = set()
        stack = list(upstream.get(target_id, ()))
        while stack:
            node = stack.pop()
            if node == source_id:
                return True
            if node in visited:
                continue
            visited.add(node)
            stack.extend(upstream.get(node, ()))
        return False
