"""编译期符号定义 — 对齐 coze_compiler.scope.symbol。

n8n 适配：
  INPUT   工作流输入（webhook body / manual 空 / chat 会话消息）
  OUTPUT  节点输出（$node["Name"].json.<path>）
  GLOBAL  全局上下文（$env / $execution / $workflow / $now）
  ITEM    数据流字段（$json.<path>，绑定到当前节点入边上游的输出）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from type_system.typeinfo import TypeInfo


class SymbolKind(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    GLOBAL = "global"
    ITEM = "item"


@dataclass(frozen=True)
class Symbol:
    name: str
    type: TypeInfo
    kind: SymbolKind
    source_node: str | None = None
    path: tuple[str, ...] = field(default_factory=tuple)

    @property
    def qualified_name(self) -> str:
        parts = [part for part in (self.source_node, self.name, *self.path) if part]
        return ".".join(parts)
