"""作用域 — 对齐 coze_compiler.scope.scope。

n8n 无词法嵌套（无 Loop/Batch 复合节点），但仍保留 WORKFLOW/NODE 两级：
- WORKFLOW scope：工作流输入 + 全部节点输出符号（n8n 中 $node["X"] 全局可引用）
- NODE scope：每个节点自己的输出符号
保留 ScopeLevel.COMPOSITE 常量以对齐规格（n8n 当前不使用）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .symbol import Symbol


class ScopeLevel(str, Enum):
    WORKFLOW = "workflow"
    NODE = "node"
    COMPOSITE = "composite"


class DuplicateSymbolError(ValueError):
    pass


@dataclass
class Scope:
    name: str
    level: ScopeLevel
    parent: Scope | None = None
    symbols: dict[str, Symbol] = field(default_factory=dict)

    def define(self, symbol: Symbol, *, replace: bool = False) -> None:
        if symbol.name in self.symbols and not replace:
            raise DuplicateSymbolError(f"symbol already defined in scope {self.name}: {symbol.name}")
        self.symbols[symbol.name] = symbol

    def resolve_local(self, name: str) -> Symbol | None:
        return self.symbols.get(name)

    def resolve(self, name: str) -> Symbol | None:
        current: Scope | None = self
        while current is not None:
            symbol = current.resolve_local(name)
            if symbol is not None:
                return symbol
            current = current.parent
        return None
