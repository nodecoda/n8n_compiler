"""具体节点类型 — 对齐 coze_compiler.ast_nodes.node_decls。

每个子类直接持有专有字段；消费方 isinstance 收窄后直接访问。

一等公民：CodeNode 持有结构化 JS AST（js_ast）+ 静态契约（js_contract）。
JS 词法作用域与工作流作用域隔离，只经两条边界穿透：
  入：contract.deps（items/$json/$input）→ input_sources（parser 按入边绑定）
  出：contract.output（return 形状）→ output_types
JS 内部变量不外泄（除非 return）。
"""
from __future__ import annotations

from typing import Any

from jscode.contract import (
    CodeEffect,
    OutputShape,
    OutputShapeKind,
    StaticContract,
)
from type_system.typeinfo import TypeInfo

from .node_type import NodeKind
from .nodes import NodeDecl

_MAIN = "main"


def _shape_to_typeinfo(shape: OutputShape) -> TypeInfo:
    """静态 return 形状 -> TypeInfo。"""
    if shape.kind == OutputShapeKind.OBJECT:
        props = {k: _type_str_to_typeinfo(v) for k, v in shape.props.items()}
        return TypeInfo.object(properties=props)
    if shape.kind == OutputShapeKind.LIST:
        return TypeInfo.array(elem=TypeInfo.any())
    if shape.kind == OutputShapeKind.VOID:
        return TypeInfo.any()
    return TypeInfo.any()


def _type_str_to_typeinfo(t: str) -> TypeInfo:
    if t == "string":
        return TypeInfo.string()
    if t == "number":
        return TypeInfo.number()
    if t == "boolean":
        return TypeInfo.boolean()
    if t == "array":
        return TypeInfo.array(elem=TypeInfo.any())
    if t == "object":
        return TypeInfo.object(properties={})
    return TypeInfo.any()


# ---------------------------------------------------------------------------
# triggers
# ---------------------------------------------------------------------------


class TriggerNode(NodeDecl):
    KIND = "trigger"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.TRIGGER)
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class ErrorTriggerNode(TriggerNode):
    KIND = "error_trigger"


# ---------------------------------------------------------------------------
# data flow
# ---------------------------------------------------------------------------


class HTTPRequestNode(NodeDecl):
    KIND = "http"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.HTTP)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class IfNode(NodeDecl):
    """IF：2 个输出端口 main_0(true) / main_1(false)，各端口形状 = 输入形状。"""

    KIND = "if"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.IF)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {
            "main_0": TypeInfo.any(),
            "main_1": TypeInfo.any(),
        })
        super().__init__(**kwargs)


class FilterNode(NodeDecl):
    KIND = "filter"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.FILTER)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class LimitNode(NodeDecl):
    KIND = "limit"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.LIMIT)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class SplitOutNode(NodeDecl):
    KIND = "split_out"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.SPLIT_OUT)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class SetNode(NodeDecl):
    """Set：输出 object（输入 + 赋值字段）。"""

    KIND = "set"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.SET)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.object(properties={})})
        super().__init__(**kwargs)


class MergeNode(NodeDecl):
    """Merge：2 个 main 输入，输出 any（合并形状静态不可知）。"""

    KIND = "merge"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.MERGE)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)

    @property
    def input_port_count(self) -> int:
        return 2


class RespondWebhookNode(NodeDecl):
    """Respond to Webhook：SINK（无输出）。"""

    KIND = "respond"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.RESPOND)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {})
        super().__init__(**kwargs)


class CodeNode(NodeDecl):
    """Code 节点 — JS AST 是 n8n 节点的一等公民。

    专有字段：
      js_contract  StaticContract（严格 acorn parse + deps/shape/effect）
      js_ast       acorn ESTree（可选保留，供下游消费；contract 是权威）
    作用域边界：
      入：js_contract.contract.deps（items/$json/$input）→ input_sources
          （parser 按入边上游绑定；$node["X"] 显式引用在表达式解析时绑定）
      出：js_contract.contract.output（return 形状）→ output_types
    """

    KIND = "code"

    def __init__(self, js_contract: StaticContract | None = None,
                 js_ast: dict[str, Any] | None = None, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.CODE)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.array(elem=TypeInfo.any())})
        shape = js_contract.contract.output if js_contract is not None else OutputShape()
        kwargs.setdefault("output_types", {_MAIN: _shape_to_typeinfo(shape)})
        self.js_contract = js_contract
        self.js_ast = js_ast
        super().__init__(**kwargs)

    @property
    def effect(self) -> CodeEffect:
        if self.js_contract is None:
            return CodeEffect.UNKNOWN
        return self.js_contract.contract.effect

    def to_config_dict(self) -> dict[str, Any]:
        base = super().to_config_dict()
        base["js"] = self.js_contract.to_dict() if self.js_contract is not None else None
        base["js_ast"] = self.js_ast
        return base


# ---------------------------------------------------------------------------
# langchain / AI
# ---------------------------------------------------------------------------


class LLMNode(NodeDecl):
    KIND = "llm"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.LLM)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class ModelNode(NodeDecl):
    KIND = "model"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.MODEL)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class OutputParserNode(NodeDecl):
    KIND = "output_parser"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.OUTPUT_PARSER)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.object(properties={})})
        super().__init__(**kwargs)


class ToolNode(NodeDecl):
    KIND = "tool"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.TOOL)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class VectorStoreNode(NodeDecl):
    KIND = "vector_store"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.VECTOR_STORE)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class RetrieverNode(NodeDecl):
    KIND = "retriever"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.RETRIEVER)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class MemoryNode(NodeDecl):
    KIND = "memory"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("node_type", NodeKind.MEMORY)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class GenericNode(NodeDecl):
    """未注册 n8n type 的泛型节点：1 入，输出端口数随注册表（1 -> main；>1 -> main_i）。"""

    KIND = "generic"

    def __init__(self, **kwargs: Any) -> None:
        from .node_type import spec_for
        kwargs.setdefault("node_type", NodeKind.GENERIC)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        spec = spec_for(kwargs.get("n8n_type", ""))
        if spec.output_ports > 1:
            kwargs.setdefault("output_types", {
                f"main_{i}": TypeInfo.any() for i in range(spec.output_ports)
            })
        else:
            kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


# ---------------------------------------------------------------------------
# synthetic entry / exit
# ---------------------------------------------------------------------------


class EntryNode(NodeDecl):
    """合成入口：工作流输入源。无输入，输出 = 工作流输入（any）。"""

    KIND = "entry"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("n8n_type", "synthetic.entry")
        kwargs.setdefault("node_type", NodeKind.TRIGGER)
        kwargs.setdefault("output_types", {_MAIN: TypeInfo.any()})
        super().__init__(**kwargs)


class ExitNode(NodeDecl):
    """合成出口：工作流输出汇。输入 = 末端节点输出。"""

    KIND = "exit"

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("n8n_type", "synthetic.exit")
        kwargs.setdefault("node_type", NodeKind.GENERIC)
        kwargs.setdefault("input_types", {_MAIN: TypeInfo.any()})
        kwargs.setdefault("output_types", {})
        super().__init__(**kwargs)
