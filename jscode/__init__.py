"""Static JavaScript compilation for n8n Code nodes (first-class subsystem).

Design (2026-08-18): n8n Code nodes carry a heavy share of workflow logic, so
JS is compiled as a first-class language: the compiler PARSES Code-node bodies
strictly with acorn (ES2022+) and treats syntax errors as compile errors with
exact positions. The compiler never evaluates JS dynamically.

Pipeline: strict parse (S1) -> ESTree analysis (S2: input deps / output shape
/ side-effect) -> typed contract + opaque payload (S3). The contract is the
authoritative view consumed by the checker and the IR; the JS source travels
as payload for downstream execution by the deploying adapter (external) or a
trusted Node subprocess (direct). Runtime policy is part of the contract.
"""
from .contract import (
    CodeEffect,
    CodePayload,
    Contract,
    FieldDep,
    OutputShape,
    OutputShapeKind,
    StaticContract,
)
from .js_ast import classify_effect, extract_deps, extract_output_shape
from .js_parser import JSInfraError, JSParseResult, JSSyntaxError, parse_js
from .js_static import compile_js_batch, compile_js_static, scan_js_source

__all__ = [
    "CodeEffect",
    "CodePayload",
    "Contract",
    "FieldDep",
    "JSInfraError",
    "JSParseResult",
    "JSSyntaxError",
    "OutputShape",
    "OutputShapeKind",
    "StaticContract",
    "classify_effect",
    "compile_js_batch",
    "compile_js_static",
    "extract_deps",
    "extract_output_shape",
    "parse_js",
    "scan_js_source",
]
