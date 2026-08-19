"""TypeInfo — 递归类型描述符（对齐 coze_compiler.type_system.typeinfo）。

描述 n8n 节点间流动的数据形状。n8n 节点参数是自由 JSON、多数节点输出形状
无法静态推导（HTTP 响应、LLM 输出），因此 ANY 承担重要角色：ANY 来源可赋给
任何目标，已知形状用于 checker 的字段引用校验。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .datatype import DataType


@dataclass
class TypeInfo:
    """递归类型描述符。"""
    type: DataType
    elem_type_info: Optional[TypeInfo] = None
    required: bool = False
    desc: str = ""
    properties: dict[str, TypeInfo] = field(default_factory=dict)

    # ---- 构造工厂 ----

    @classmethod
    def string(cls, required: bool = False, desc: str = "") -> TypeInfo:
        return cls(type=DataType.STRING, required=required, desc=desc)

    @classmethod
    def number(cls, required: bool = False, desc: str = "") -> TypeInfo:
        return cls(type=DataType.NUMBER, required=required, desc=desc)

    @classmethod
    def boolean(cls, required: bool = False, desc: str = "") -> TypeInfo:
        return cls(type=DataType.BOOLEAN, required=required, desc=desc)

    @classmethod
    def binary(cls, required: bool = False, desc: str = "") -> TypeInfo:
        return cls(type=DataType.BINARY, required=required, desc=desc)

    @classmethod
    def any(cls, required: bool = False, desc: str = "") -> TypeInfo:
        return cls(type=DataType.ANY, required=required, desc=desc)

    @classmethod
    def array(cls, elem: TypeInfo, required: bool = False, desc: str = "") -> TypeInfo:
        return cls(type=DataType.ARRAY, elem_type_info=elem, required=required, desc=desc)

    @classmethod
    def object(cls, properties: dict[str, TypeInfo], required: bool = False, desc: str = "") -> TypeInfo:
        return cls(type=DataType.OBJECT, properties=properties, required=required, desc=desc)

    # ---- 序列化 / 反序列化 ----

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TypeInfo:
        if not isinstance(d, dict):
            return cls.any()
        ti = cls(type=DataType.from_str(d.get("type", "any")))
        ti.required = bool(d.get("required", False))
        ti.desc = d.get("desc", "")
        if d.get("elem_type_info"):
            ti.elem_type_info = cls.from_dict(d["elem_type_info"])
        if d.get("properties"):
            ti.properties = {k: cls.from_dict(v) for k, v in d["properties"].items()}
        return ti

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type.value, "required": self.required}
        if self.desc:
            d["desc"] = self.desc
        if self.elem_type_info is not None:
            d["elem_type_info"] = self.elem_type_info.to_dict()
        if self.properties:
            d["properties"] = {k: v.to_dict() for k, v in self.properties.items()}
        return d

    # ---- 类型判定 ----

    def is_simple(self) -> bool:
        return self.type in (DataType.STRING, DataType.NUMBER, DataType.BOOLEAN)

    def is_array(self) -> bool:
        return self.type == DataType.ARRAY

    def is_object(self) -> bool:
        return self.type == DataType.OBJECT

    def is_any(self) -> bool:
        return self.type == DataType.ANY

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"TypeInfo({self.type.value}{'[]' if self.is_array() else ''})"
