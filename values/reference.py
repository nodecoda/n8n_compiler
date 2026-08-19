"""引用系统 — 对齐 coze_compiler.values.reference。

描述 n8n 参数值的三种来源：
1. 静态字面量（literal JSON）
2. 节点输出引用（Ref + from_node_key）：表达式 $node["X"].json.path 或数据流 $json.path
3. 全局变量引用（Ref + variable_type）：$env.X / $execution.* 等
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .variable import GlobalVarType


@dataclass
class Reference:
    """编译期引用：指向某节点的输出路径，或全局变量。"""
    from_node_key: str
    from_path: list[str] = field(default_factory=list)
    variable_type: GlobalVarType | None = None

    def is_global_variable(self) -> bool:
        return self.variable_type is not None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "from_node_key": self.from_node_key,
            "from_path": list(self.from_path),
        }
        if self.variable_type is not None:
            result["variable_type"] = self.variable_type.value
        return result

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Reference:
        vt = d.get("variable_type")
        return cls(
            from_node_key=d.get("from_node_key", ""),
            from_path=list(d.get("from_path", []) or []),
            variable_type=GlobalVarType(vt) if vt else None,
        )


@dataclass
class Source:
    """一个输入数据源：块输出引用（ref）或字面量。"""
    ref: Reference | None = None
    literal: Any = None

    def is_literal(self) -> bool:
        return self.ref is None

    def to_dict(self) -> dict[str, Any]:
        if self.ref is not None:
            return {"ref": self.ref.to_dict()}
        return {"literal": self.literal}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Source:
        if "ref" in d:
            return cls(ref=Reference.from_dict(d["ref"]))
        return cls(literal=d.get("literal"))


@dataclass
class FieldInfo:
    """一个输入字段：目标路径 + 来源。"""
    path: list[str]
    source: Source

    def to_dict(self) -> dict[str, Any]:
        return {"path": list(self.path), "source": self.source.to_dict()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FieldInfo:
        return cls(path=list(d.get("path", [])), source=Source.from_dict(d.get("source", {})))
