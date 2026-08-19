"""n8n 表达式上下文变量 — 对应 coze_compiler.values.variable.GlobalVarType。

n8n 表达式上下文（@n8n/expression-runtime 注入）：
  $json / $input     当前节点输入数据
  $node["Name"]      引用其他节点输出
  $env.X             环境变量（全局）
  $execution         执行上下文（id/mode/resumeUrl 等）
  $workflow          工作流上下文（id/name/active）
  $now / $today      时间
  $items             多输入聚合
  $parameters        当前节点参数
"""
from __future__ import annotations

from enum import Enum


class GlobalVarType(str, Enum):
    ENV = "env"
    EXECUTION = "execution"
    WORKFLOW = "workflow"
    NOW = "now"
    PARAMETERS = "parameters"
    ITEMS = "items"


class RefSourceType(str, Enum):
    NODE_OUTPUT = "node_output"        # $node["X"].json.path / 上游数据流
    GLOBAL_VARIABLE = "global_variable"  # $env / $execution / $workflow ...

    @classmethod
    def from_str(cls, s: str) -> RefSourceType:
        try:
            return cls(s)
        except ValueError:
            return cls.NODE_OUTPUT
