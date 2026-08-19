#!/usr/bin/env python3
"""A2 真实执行验证：生成反编译探针产物 + 打印远端 n8n 验证命令。

目的：证明「编译器认为 OK 的工作流真能被 n8n 加载并跑通」。本脚本只负责
本地侧（产物生成 + 断言标准），n8n 运行在远端/容器（见打印出的命令）。

用法:
    python3 scripts/execute_verify.py build [--output out.json] [--id wf-id]

流程（本机，无第三方依赖）:
    n8n JSON（helpers fixture）-> parse -> compile -> typed IR -> decompile
    -> 反编译工作流 JSON；随后在装有 n8n(docker) 的机器上 import + execute。

验证链（远端，n8nio/n8n 镜像）:
    n8n import:workflow --input=<out.json>   # 加载校验（抓形状缺口）
    n8n execute --id=<id> --rawOutput         # 真实执行（抓语义缺口）
    grep '"result": 42'                        # 断言 Code 节点输出

2026-08-18 首次跑通记录（nodecoda-production，腾讯云内网 docker 源）:
    import: "Successfully imported 1 workflow."
    execute: status=success finished=true lastNodeExecuted=Code
    输出: "result": 42
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# scripts/ 与 tests/ 同仓库，经 tests/__init__.py 统一引导（禁 sys.path hack）
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))  # 仅脚本入口引导（与 cli.py 同模式）

from compiler.workflow import compile_ast
from parser.workflow import parse_workflow
from runtime.decompile import decompile_to_workflow
from tests.helpers import chain_workflow, code_node, manual_trigger_node


def build_probe(workflow_id: str) -> dict:
    """Manual Trigger -> Code 探针（避开 webhook 触发在 execute 下挂起）。"""
    wf = chain_workflow(
        [manual_trigger_node("Trigger"), code_node("Code", "return { result: 42 };")],
        [("Trigger", "Code")],
    )
    wf["name"] = "a2-probe"
    ir = compile_ast(parse_workflow(wf), workflow_id=workflow_id, version="1").to_dict()
    return decompile_to_workflow(ir, name="a2-probe")


def print_remote_commands(out: Path, workflow_id: str) -> None:
    print("\n远端验证命令（在装有 docker + n8nio/n8n 的机器上，产物先拷过去）:\n")
    print(f"  scp {out} <remote>:/tmp/{out.name}")
    print("  ssh <remote> 'mkdir -p /tmp/n8n-data && chmod 777 /tmp/n8n-data && \\")
    print("    docker run --rm --entrypoint sh -v /tmp:/data -v /tmp/n8n-data:/home/node/.n8n \\")
    print("      -e N8N_DIAGNOSTICS_ENABLED=false n8nio/n8n -c \\")
    print(f"      \"n8n import:workflow --input=/data/{out.name} && \\")
    print(f"       n8n execute --id={workflow_id} --rawOutput\"'")
    print("\n期望: import 输出 \"Successfully imported 1 workflow.\"；")
    print("      execute 输出含 \\\"result\\\": 42；status=success。")


def main() -> int:
    ap = argparse.ArgumentParser(description="A2 真实执行验证（产物生成 + 命令打印）")
    ap.add_argument("action", choices=["build"], help="生成反编译探针产物")
    ap.add_argument("--output", default="/tmp/a2-decompiled.json", help="产物路径")
    ap.add_argument("--id", default="a2-probe-001", help="工作流 id（导入后 execute 用）")
    args = ap.parse_args()

    wf = build_probe(args.id)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(wf, indent=2, ensure_ascii=False))
    print(f"产物已生成: {out}")
    print(f"  id: {wf['id']} | nodes: {[n['name'] for n in wf['nodes']]}")
    print_remote_commands(out, args.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
