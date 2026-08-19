"""节点配置与异常策略 — 对齐 coze_compiler.ast_nodes.configs.ExceptionConfig。

n8n 节点级策略：
  on_error: continueRegularOutput | continueErrorOutput | stopWorkflow
  retryOnFail / maxTries / waitBetweenTries
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExceptionConfig:
    """异常配置（对齐 coze ExceptionConfig 的序列化形状）。"""
    timeout_ms: int | None = None
    max_retry: int | None = None
    data_on_err: str | None = None
    process_type: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.timeout_ms is not None:
            result["timeout_ms"] = self.timeout_ms
        if self.max_retry is not None:
            result["max_retry"] = self.max_retry
        if self.data_on_err is not None:
            result["data_on_err"] = self.data_on_err
        if self.process_type is not None:
            result["process_type"] = self.process_type
        return result


@dataclass
class N8NErrorPolicy:
    """n8n 节点错误策略。"""
    on_error: str = "stopWorkflow"           # continueRegularOutput | continueErrorOutput | stopWorkflow
    retry_on_fail: bool = False
    max_tries: int | None = None
    wait_between_tries: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"on_error": self.on_error}
        if self.retry_on_fail:
            result["retry_on_fail"] = True
            if self.max_tries is not None:
                result["max_tries"] = self.max_tries
            if self.wait_between_tries is not None:
                result["wait_between_tries"] = self.wait_between_tries
        return result

    @classmethod
    def from_node(cls, raw: dict[str, Any]) -> N8NErrorPolicy:
        on_error = raw.get("onError", "stopWorkflow")
        if on_error not in ("continueRegularOutput", "continueErrorOutput", "stopWorkflow"):
            on_error = "stopWorkflow"
        return cls(
            on_error=on_error,
            retry_on_fail=bool(raw.get("retryOnFail", False)),
            max_tries=raw.get("maxTries"),
            wait_between_tries=raw.get("waitBetweenTries"),
        )
