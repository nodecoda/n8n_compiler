"""连接 — 对齐 coze_compiler.ast_nodes.connection。

n8n IConnections 形状：connections[源节点名].main[i][j]，其中 i = 输出端口
索引（IF 的 true=0/false=1），j = 该端口上的第 j 条边。目标节点没有显式输入
端口（n8n 输入恒为 main 单一输入，Merge 等多输入节点也是多条边汇入同一输入）。
to_port 保留字段以对齐规格（n8n 恒为 "main"）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Connection:
    from_node: str
    from_port: str
    to_node: str
    to_port: str = "main"
    to_index: int = 0  # 目标输入端口索引（Merge 多输入：0/1；单输入恒 0）
    conn_type: str = "main"  # n8n 连接类型：main / ai_languageModel / ai_tool 等（P1-1c）

    @property
    def identity(self) -> str:
        return f"{self.from_node}|{self.from_port}|{self.to_node}|{self.to_index}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_node": self.from_node,
            "from_port": self.from_port,
            "to_node": self.to_node,
            "to_port": self.to_port,
            "to_index": self.to_index,
            "conn_type": self.conn_type,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Connection:
        return cls(
            from_node=d["from_node"],
            from_port=str(d.get("from_port", "0")),
            to_node=d["to_node"],
            to_port=str(d.get("to_port", "main")),
            to_index=int(d.get("to_index", 0)),
            conn_type=str(d.get("conn_type", "main")),
        )
