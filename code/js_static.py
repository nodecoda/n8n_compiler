"""High-level static JS compilation for n8n Code nodes.

Pipeline: strict acorn parse (S1) -> ESTree analysis (S2: deps / output shape
/ effect) -> typed contract + opaque payload (S3).

The compiler never evaluates JS dynamically. Syntax errors are compile errors
with exact line/column; analysis degrades specific unknowns to ANY / UNKNOWN
with warnings, never guesses.
"""
from __future__ import annotations

from typing import List, Tuple

from .contract import (
    CodeEffect,
    CodePayload,
    Contract,
    OutputShape,
    OutputShapeKind,
    StaticContract,
)
from .js_ast import (
    classify_effect,
    collect_warnings,
    extract_deps,
    extract_output_shape,
    reject_module_syntax,
)
from .js_parser import JSInfraError, JSParseResult, parse_js, parse_js_batch

_MODE_HINTS = {
    "runOnceForAllItems": "items is the full input array",
    "runOnceForEachItem": "item is the current input item",
    # P1-2（v4）：AI 链 supplyData 工厂（@n8n/n8n-nodes-langchain.code）——
    # 工厂函数 return 组件实例（FakeEmbeddings/FakeChatModel 等），
    # 无 items/$json 输入；输出形状 = 工厂返回对象。
    "factory": "supplyData factory: returns a component instance (no items input)",
    # P2-4（v5）：Code Tool（toolCode）——工具函数签名 (query) => result，
    # 由 agent 在工具调用时执行，无 items 输入（n8n runCodeForTool）。
    "tool": "tool function: (query, ...) => result (Code Tool; no items input)",
}


def _error_contract(source: str, errs: tuple[str, ...], runtime: str) -> StaticContract:
    return StaticContract(
        contract=Contract(
            deps=[],
            output=OutputShape(kind=OutputShapeKind.ANY),
            effect=CodeEffect.UNKNOWN,
            runtime=runtime,
        ),
        payload=CodePayload(language="js", source=source),
        errors=errs,
    )


def _contract_from_ast(ast: dict, source: str, mode: str, runtime: str) -> StaticContract:
    """S2+S3（纯 Python）：ESTree -> deps/shape/effect + 语义编译错误/warning。

    import/export/动态 import() 是 n8n 运行时错误，此处前置为编译错误
    （errors 非空即编译失败）；网络/fs/require、动态下标是 warning。
    """
    deps = extract_deps(ast)
    shape = extract_output_shape(ast)
    effect = classify_effect(ast)
    errors = tuple(reject_module_syntax(ast))
    warnings = collect_warnings(ast)
    hint = _MODE_HINTS.get(mode)
    if hint:
        warnings.append(f"mode {mode}: {hint}")
    return StaticContract(
        contract=Contract(deps=deps, output=shape, effect=effect, runtime=runtime),
        payload=CodePayload(language="js", source=source),
        errors=errors,
        warnings=tuple(warnings),
    )


def compile_js_static(
    source: str,
    *,
    mode: str = "runOnceForAllItems",
    runtime: str = "external",
) -> StaticContract:
    """Strictly parse + statically analyze a Code-node JS body.

    Returns StaticContract with errors=() on success; errors=(...) carrying
    exact line/col syntax diagnostics on parse failure. Raises JSInfraError
    when the acorn bridge (Node) is unavailable — infrastructure, not source,
    failure.
    """
    result: JSParseResult = parse_js(source)
    if not result.ok:
        errs = tuple(f"{e.line}:{e.col}: {e.message}" for e in result.errors)
        return _error_contract(source, errs, runtime)
    return _contract_from_ast(result.ast or {}, source, mode, runtime)


def compile_js_batch(
    sources: list[str],
    *,
    modes: list[str] | None = None,
    runtime: str = "external",
) -> list[tuple[StaticContract, dict | None]]:
    """批量编译：一次 acorn 进程解析全部源码（Node 启动开销摊薄）。

    返回 [(contract, estree_ast)]，与 sources 顺序一致；单脚本语法错误以
    contract.errors 呈现，不影响其他脚本。
    """
    results = parse_js_batch(sources)
    out: list[tuple[StaticContract, dict | None]] = []
    for index, result in enumerate(results):
        source = sources[index]
        mode = modes[index] if modes and index < len(modes) else "runOnceForAllItems"
        if not result.ok:
            errs = tuple(f"{e.line}:{e.col}: {e.message}" for e in result.errors)
            out.append((_error_contract(source, errs, runtime), None))
        else:
            out.append((_contract_from_ast(result.ast or {}, source, mode, runtime), result.ast))
    return out


def scan_js_source(source: str) -> Tuple[List, List[str], List[str]]:
    """Legacy-compat: strict parse; returns (tokens_placeholder, errors, warnings).

    The acorn path produces no token stream; errors carry line/col messages.
    """
    result = compile_js_static(source)
    if result.errors:
        return [], list(result.errors), []
    return [], [], list(result.warnings)
