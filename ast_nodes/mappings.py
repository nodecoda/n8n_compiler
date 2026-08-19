"""n8n type -> NodeDecl 类工厂 + IR 反序列化 — 对齐 coze_compiler.ast_nodes.node_decls 的 NODE_CLASS_BY_TYPE。

未注册 type -> GenericNode（白名单泛型，不深查参数）。
"""
from __future__ import annotations

from typing import Any, Optional

from code.contract import Contract, FieldDep, OutputShape, OutputShapeKind, StaticContract
from code.contract import CodeEffect, CodePayload
from values.reference import FieldInfo
from type_system.typeinfo import TypeInfo

from .node_decls import (
    CodeNode,
    EntryNode,
    ErrorTriggerNode,
    ExitNode,
    FilterNode,
    GenericNode,
    HTTPRequestNode,
    IfNode,
    LimitNode,
    LLMNode,
    MemoryNode,
    MergeNode,
    ModelNode,
    OutputParserNode,
    RespondWebhookNode,
    RetrieverNode,
    SetNode,
    SplitOutNode,
    ToolNode,
    TriggerNode,
    VectorStoreNode,
)
from .node_type import NodeKind
from .nodes import NodeDecl


def node_class_for(n8n_type: str) -> type[NodeDecl]:
    if n8n_type in ("synthetic.entry",):
        return EntryNode
    if n8n_type in ("synthetic.exit",):
        return ExitNode
    kind = _kind_for(n8n_type)
    return _KIND_TO_CLASS.get(kind, GenericNode)


_KIND_TO_CLASS: dict[NodeKind, type[NodeDecl]] = {
    NodeKind.TRIGGER: TriggerNode,
    NodeKind.ERROR_TRIGGER: ErrorTriggerNode,
    NodeKind.HTTP: HTTPRequestNode,
    NodeKind.IF: IfNode,
    NodeKind.FILTER: FilterNode,
    NodeKind.LIMIT: LimitNode,
    NodeKind.SET: SetNode,
    NodeKind.MERGE: MergeNode,
    NodeKind.SPLIT_OUT: SplitOutNode,
    NodeKind.CODE: CodeNode,
    NodeKind.RESPOND: RespondWebhookNode,
    NodeKind.LLM: LLMNode,
    NodeKind.MODEL: ModelNode,
    NodeKind.OUTPUT_PARSER: OutputParserNode,
    NodeKind.TOOL: ToolNode,
    NodeKind.VECTOR_STORE: VectorStoreNode,
    NodeKind.RETRIEVER: RetrieverNode,
    NodeKind.MEMORY: MemoryNode,
}


def _kind_for(n8n_type: str) -> NodeKind:
    from .node_type import spec_for
    return spec_for(n8n_type).kind


def load_typed_node(node_dict: dict[str, Any]) -> NodeDecl:
    """从 IR node dict 反序列化为强类型 NodeDecl（对齐 coze load_typed_node）。"""
    config = node_dict.get("config", {})
    n8n_type = node_dict.get("n8n_type") or config.get("n8n_type", "")
    cls = node_class_for(n8n_type)
    kwargs: dict[str, Any] = {
        "key": node_dict["key"],
        "n8n_type": n8n_type,
        "name": node_dict["name"],
        "parent_key": node_dict.get("parent_key"),
    }
    if "type_version" in config:
        kwargs["type_version"] = config["type_version"]
    if "position" in config:
        pos = config["position"]
        kwargs["position"] = tuple(pos) if isinstance(pos, list) else pos
    if "parameters" in config:
        kwargs["parameters"] = config["parameters"]
    if cls is CodeNode:
        js = config.get("js")
        if js:
            kwargs["js_contract"] = _contract_from_dict(js)
        if config.get("js_ast"):
            kwargs["js_ast"] = config["js_ast"]
    node = cls(**kwargs)
    node.input_types = {k: TypeInfo.from_dict(v) for k, v in node_dict.get("input_types", {}).items()}
    node.output_types = {k: TypeInfo.from_dict(v) for k, v in node_dict.get("output_types", {}).items()}
    # 回填依赖/引用（对齐 coze load_typed_config：使运行时无需维护并行的 node dict
    # 表示）。input_sources 承载表达式引用 + Code 依赖两种绑定，缺省空列表。
    node.input_sources = [FieldInfo.from_dict(s) for s in node_dict.get("input_sources", [])]
    node.output_sources = [FieldInfo.from_dict(s) for s in node_dict.get("output_sources", [])]
    return node


def _contract_from_dict(js: dict[str, Any]) -> StaticContract:
    """IR js contract dict -> StaticContract。"""
    c = js.get("contract", {})
    out = c.get("output", {})
    shape = OutputShape(
        kind=OutputShapeKind(out.get("kind", "any")),
        props=dict(out.get("props", {})),
        elem=out.get("elem"),
    )
    effect = CodeEffect(c.get("effect", "unknown"))
    payload = js.get("payload", {})
    # P2-3：contract.deps 读写对称——IR 序列化了 deps，反序列化必须读回
    # （曾硬编码 deps=[] 丢弃，依赖信息仅靠 node 级 input_sources 存活）
    deps = [
        FieldDep(base=item.get("base", ""), path=tuple(item.get("path") or []))
        for item in (c.get("deps") or [])
        if isinstance(item, dict)
    ]
    return StaticContract(
        contract=Contract(
            deps=deps,
            output=shape,
            effect=effect,
            runtime=c.get("runtime", "external"),
        ),
        payload=CodePayload(
            language=payload.get("language", "js"),
            source=payload.get("source", ""),
        ),
        errors=tuple(js.get("errors", [])),
        warnings=tuple(js.get("warnings", [])),
    )
