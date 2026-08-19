"""raw n8n node dict -> NodeDecl（对齐 coze_compiler.parser.node_adaptors）。

按 n8n type 分派创建强类型节点；未知 type 落 GenericNode。
Code 节点走一等公民路径：严格 acorn parse -> StaticContract + ESTree ast。
"""
from __future__ import annotations

from typing import Any, Optional

from ast_nodes.configs import N8NErrorPolicy
from ast_nodes.mappings import node_class_for
from ast_nodes.node_decls import CodeNode, NodeDecl
from code import compile_js_static, parse_js

_CODE_PARAM_FIELDS = ("jsCode", "code")
# P1-2（v4）：AI 链内联 Code 类型——取源走 supplyData 工厂分流
_LANGCHAIN_CODE_TYPE = "@n8n/n8n-nodes-langchain.code"
# P2-4（v5）：Code Tool 类型——顶层 jsCode/pythonCode（非 supplyData 嵌套）
_TOOLCODE_TYPE = "@n8n/n8n-nodes-langchain.toolCode"


class UnsupportedSourceError(ValueError):
    """源码使用了编译器 v1 不支持的特性（合法但不受支持）——与畸形输入区分。

    P3-10：CLI 单独映射为退出码 1（源/校验错误），而非 2（畸形输入）；
    stderr 前缀 "unsupported source:" 供脚本化消费方判别。
    """


def _code_source(parameters: dict[str, Any], *, node_type: str = "",
                 branch: Optional[str] = None) -> tuple[str, str]:
    """从 parameters 取 JS 源码与执行模式（v1 仅 JS；Python 模式后续）。

    按节点类型分流：
      - n8n-nodes-base.code：顶层 jsCode/code，mode = parameters.mode；
      - @n8n/n8n-nodes-langchain.code（P1-2，v4 / P2-1，v5 双模式）：
        * supplyData 变体：parameters.code.supplyData.code（固定集合包装），
          mode "factory"——工厂返回组件实例，无 items/$json 输入；
        * execute 变体：parameters.code.execute.code，mode "runOnceForAllItems"
          ——Main 输出形态，runCodeAllItems + addItems:true（items 全量数组）；
        * branch 参数由调用方按节点实际输出连接判定（main 出边 → execute，
          仅 ai_* 出边 → supplyData）；None = 兼容旧行为（supplyData 优先）。
          双分支并存时以连接为准，消除取错源的静默错编译亚型。
        旧版 n8n 导出可能是顶层字符串，防御性兜底（factory）。
      - @n8n/n8n-nodes-langchain.toolCode（P2-4，v5）：顶层 jsCode
        （language=javaScript），mode "tool"——工具函数 (query) => result，
        由 agent 工具调用时执行；pythonCode → UnsupportedSourceError（与
        base.code python 同纪律，不静默降级）。
    """
    if node_type == _LANGCHAIN_CODE_TYPE:
        return _langchain_code_source(parameters, branch)
    if node_type == _TOOLCODE_TYPE:
        return _toolcode_source(parameters)
    language = parameters.get("language", "javaScript")
    mode = parameters.get("mode", "runOnceForAllItems")
    for field in _CODE_PARAM_FIELDS:
        src = parameters.get(field)
        if isinstance(src, str) and src.strip():
            return src, mode
    if language in ("python", "pythonNative"):
        # 源不受支持（与 JSInfraError"桥不可用"的基础设施错误区分）：
        # v1 明确报错，不动态执行
        raise UnsupportedSourceError(
            f"Code node Python mode ({language}) is not supported by the static JS "
            "compiler (v1). Use javaScript mode or lower the python code into the workflow."
        )
    return "", mode


def _langchain_code_source(parameters: dict[str, Any],
                           branch: Optional[str]) -> tuple[str, str]:
    """langchain.code 双模式取源（P2-1，v5）。branch: "execute"/"supplyData"/None。"""
    code_param = parameters.get("code")
    if isinstance(code_param, dict):
        order = ("supplyData", "execute") if branch is None else (branch,)
        for name in order:
            src = (code_param.get(name) or {}).get("code")
            if isinstance(src, str) and src.strip():
                mode = "factory" if name == "supplyData" else "runOnceForAllItems"
                return src, mode
        if branch is not None:
            other = "execute" if branch == "supplyData" else "supplyData"
            src = (code_param.get(other) or {}).get("code")
            if isinstance(src, str) and src.strip():
                mode = "factory" if other == "supplyData" else "runOnceForAllItems"
                return src, mode
    if isinstance(code_param, str) and code_param.strip():
        return code_param, "factory"
    return "", "factory"


def _toolcode_source(parameters: dict[str, Any]) -> tuple[str, str]:
    """Code Tool 取源（P2-4，v5）：顶层 jsCode/pythonCode，language 分流。"""
    language = parameters.get("language", "javaScript")
    if language == "javaScript":
        src = parameters.get("jsCode")
        if isinstance(src, str) and src.strip():
            return src, "tool"
        return "", "tool"
    raise UnsupportedSourceError(
        f"Code Tool Python mode ({language}) is not supported by the static JS "
        "compiler (v1). Use javaScript mode or lower the tool logic into the workflow."
    )


def adapt_node(raw: dict[str, Any], js_cache: Optional[dict] = None) -> NodeDecl:
    """反序列化一个 n8n 节点为强类型 NodeDecl。key = 节点名（n8n 唯一约束）。

    js_cache: 预编译的 Code 节点缓存 {name: (StaticContract, estree_ast|None)}，
    由 parse_workflow 批量填充（一次 acorn 进程编译全部 Code 节点）。
    """
    n8n_type = raw.get("type", "")
    name = raw.get("name", "")
    key = name  # n8n connections 以 name 索引，name 在编辑器中保证唯一
    cls = node_class_for(n8n_type)
    kwargs: dict[str, Any] = {
        "key": key,
        "n8n_type": n8n_type,
        "name": name,
        "type_version": float(raw.get("typeVersion", 1)),
    }
    pos = raw.get("position")
    if isinstance(pos, list) and len(pos) >= 2:
        kwargs["position"] = (float(pos[0]), float(pos[1]))
    parameters = raw.get("parameters") or {}
    kwargs["parameters"] = parameters

    if cls is CodeNode:
        source, mode = _code_source(parameters, node_type=n8n_type)
        js_ast = None
        if source.strip():
            if js_cache is not None and name in js_cache:
                static, js_ast = js_cache[name]
            else:
                static = compile_js_static(source, mode=mode)
                if static.ok:
                    js_ast = parse_js(source).ast  # 一等公民：保留 ESTree
            kwargs["js_contract"] = static
            kwargs["js_ast"] = js_ast
        else:
            from code import StaticContract, Contract, OutputShape, OutputShapeKind, CodePayload, CodeEffect
            kwargs["js_contract"] = StaticContract(
                contract=Contract(output=OutputShape(kind=OutputShapeKind.VOID), effect=CodeEffect.UNKNOWN),
                payload=CodePayload(source=""),
                errors=("Code node has no JS source",),
            )

    node = cls(**kwargs)
    node.error_policy = N8NErrorPolicy.from_node(raw)
    node.credentials = dict(raw.get("credentials") or {})
    return node
