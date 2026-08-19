"""n8n 工作流编译器命令行入口 — 对齐 coze_compiler.cli。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from checker.validator import WorkflowValidationError, validate_workflow
from compiler.workflow import compile_ast
from jscode.js_parser import JSInfraError
from parser.node_adaptors import UnsupportedSourceError
from parser.workflow import parse_workflow
from typed_ir import reject_non_finite


def _read_json(path: Path) -> dict:
    """读取 n8n 工作流 JSON（兼容 {"workflow": {...}} 包装）。"""
    # P2-9：NaN/Infinity 前置显式拒绝（allow_nan=True 会静默带进 IR，编译
    # 后期才炸且报错误导；parse_constant 让输入侧报错更早更清晰）
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_non_finite)
    if not isinstance(value, dict):
        raise TypeError(f"{path} must be a JSON object")
    return value.get("workflow", value)


def command_check(args: argparse.Namespace) -> int:
    """解析 + 静态校验 n8n 工作流 JSON。"""
    data = _read_json(args.input)
    ast = parse_workflow(data)
    issues = validate_workflow(ast)
    if issues:
        print(json.dumps(
            [i.__dict__ for i in issues],
            ensure_ascii=False, indent=2,
        ))
        return 1
    print("OK")
    return 0


def command_compile(args: argparse.Namespace) -> int:
    """编译为 n8n-typed-ir v1 文档。"""
    data = _read_json(args.input)
    ast = parse_workflow(data)
    compiled = compile_ast(ast, workflow_id=args.workflow_id, version=args.version)
    # P2-2（v5）：settings 契约 warning 打 stderr（不污染 stdout IR，不阻断）
    for warning in compiled.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    payload = compiled.to_json(indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI。"""
    parser = argparse.ArgumentParser(prog="n8n-compiler")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="parse and statically validate an n8n workflow JSON")
    check.add_argument("input", type=Path)
    check.set_defaults(handler=command_check)

    compile_cmd = subparsers.add_parser("compile", help="compile an n8n workflow JSON to typed IR")
    compile_cmd.add_argument("input", type=Path)
    compile_cmd.add_argument("-o", "--output", type=Path)
    compile_cmd.add_argument("--workflow-id", default="")
    compile_cmd.add_argument("--version", default="")
    compile_cmd.set_defaults(handler=command_compile)
    return parser


def main(argv: list[str] | None = None) -> int:
    """入口。

    退出码约定（P2-7，供脚本化消费方区分错误类别）：
      0 成功；1 工作流校验/源错误（check 校验不过、编译被 validator 拒绝）；
      2 输入/用法错误（文件不可读、JSON 畸形含 NaN、参数错）；
      3 基础设施错误（acorn/Node 桥不可用——与源错误区分，如 CI 装 Node）。
      1 亦用于「合法但 v1 不支持」的源（如 Python 模式 Code，P3-10）。
    """
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except WorkflowValidationError as exc:
        print(f"validation error: {exc}", file=sys.stderr)
        return 1
    except UnsupportedSourceError as exc:
        # 合法但 v1 不支持（Python 模式 Code）——源级问题，非畸形输入
        print(f"unsupported source: {exc}", file=sys.stderr)
        return 1
    except JSInfraError as exc:
        print(f"infrastructure error: {exc}", file=sys.stderr)
        return 3
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
