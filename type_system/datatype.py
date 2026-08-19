"""N8NDataType 枚举 — n8n 工作流数据类型（对齐 coze_compiler.type_system.datatype）。

n8n 与 Coze 的差异：
- integer/number 在 n8n 里都是 JSON number，统一为 NUMBER
- 无 time 类型
- binary 对应 n8n 的 binary data（文件/附件）
- any = 编译期未知（n8n 节点参数是自由 JSON，多数输出形状不可静态推导）
"""
from __future__ import annotations

from enum import Enum


class DataType(str, Enum):
    STRING = "string"
    NUMBER = "number"    # JSON number（n8n 不区分 int/float）
    BOOLEAN = "boolean"
    OBJECT = "object"
    ARRAY = "list"       # JSON 数组
    BINARY = "binary"    # n8n binary data（文件/附件）
    ANY = "any"          # 编译期未知

    @classmethod
    def from_str(cls, s: str) -> DataType:
        s = (s or "").strip().lower()
        if s in ("array", "list"):
            return cls.ARRAY
        if s in ("int", "integer", "long", "float", "number", "double"):
            return cls.NUMBER
        if s in ("bool", "boolean"):
            return cls.BOOLEAN
        if s in ("file", "files"):
            return cls.BINARY
        if s in ("object", "map", "dict"):
            return cls.OBJECT
        if s in ("any", "unknown", ""):
            return cls.ANY
        try:
            return cls(s)
        except ValueError:
            return cls.ANY
