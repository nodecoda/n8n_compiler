#!/usr/bin/env python3
"""A2 矩阵化：多场景真实执行验证（IR -> n8n JSON -> n8n 实例执行 -> 断言）。

把 A2 的"最小探针"扩成覆盖核心执行语义的场景矩阵：Set 赋值、IF 多输出、
Switch 多路由 + fallback、Merge 多输入合并、表达式插值、Code 链。每个场景
统一形状：

    Manual Trigger -> Seed Code(可控输入) -> 被测节点链 -> Out Code(断言值)

执行链：本地 parse -> compile -> decompile 生成产物；远端（装有
n8nio/n8n 的机器）import + execute --rawOutput；拉回结果后断言 Out 节点输出。

用法:
    python3 scripts/execute_matrix.py build [-o out_dir]      # 生成全部场景产物
    python3 scripts/execute_matrix.py remote-cmd [out_dir]     # 打印远端执行命令
    python3 scripts/execute_matrix.py assert results.json      # 断言执行结果
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))  # 仅脚本入口引导（与 cli.py 同模式）

from compiler.workflow import compile_ast
from parser.workflow import parse_workflow
from runtime.decompile import decompile_to_workflow

OUT_NODE = "Out"


def _node(name: str, n8n_type: str, type_version: int, parameters: dict,
          *, x: int = 0, y: int = 2, extra: dict | None = None) -> dict:
    node = {"name": name, "type": n8n_type, "typeVersion": type_version,
            "position": [x, y], "parameters": parameters}
    if extra:
        node.update(extra)
    return node


def _seed(source: str, name: str = "Seed") -> dict:
    """场景输入源：Code 节点产出确定性数据。"""
    return _node(name, "n8n-nodes-base.code", 2,
                 {"mode": "runOnceForAllItems", "jsCode": source})


def _out(source: str) -> dict:
    """断言节点：产出可断言的输出。"""
    return _node(OUT_NODE, "n8n-nodes-base.code", 2,
                 {"mode": "runOnceForAllItems", "jsCode": source})


def _build(nodes: list[dict], edges: list[tuple[str, str, int, int]]) -> dict:
    """nodes + 显式 main 连接。

    边 = (src, dst, src_port_index, to_index)；src_port_index 是源输出端口
    （IF true=0/false=1），to_index 是目标输入端口（Merge 双输入 0/1）。
    """
    connections: dict[str, dict] = {}
    for src, dst, port, to_index in edges:
        connections.setdefault(src, {"main": []})
        while len(connections[src]["main"]) <= port:
            connections[src]["main"].append([])
        connections[src]["main"][port].append(
            {"node": dst, "type": "main", "index": to_index})
    return {"name": "mx", "nodes": nodes, "connections": connections}


def _finalize(nodes: list[dict], edges: list[tuple[str, str, int, int]]) -> dict:
    """_build + 注入 Manual Trigger（n8n execute 需要 trigger 起始节点）。

    统一形状：Manual Trigger -> Seed(Code) -> 被测链 -> Out(Code)。
    """
    wf = _build(nodes, edges)
    trigger = {"name": "Trigger", "type": "n8n-nodes-base.manualTrigger",
               "typeVersion": 1, "position": [0, 0], "parameters": {}}
    wf["nodes"].insert(0, trigger)
    wf["connections"]["Trigger"] = {"main": [[{"node": "Seed", "type": "main", "index": 0}]]}
    return wf


# ---------------------------------------------------------------------------
# 场景定义：name -> (workflow dict, 断言函数(run_data) -> None/抛异常)
# ---------------------------------------------------------------------------

def _scenarios() -> list[dict]:
    s = []

    # 1) Set 赋值 + 表达式插值（== {{ $json.x }}）
    s.append({
        "name": "set_assignments",
        "wf": _finalize(
            [_seed("return { x: 5 };"), _node("S", "n8n-nodes-base.set", 3.5, {
                "assignments": {"assignments": [
                    {"name": "y", "value": "={{ $json.x }}", "type": "number"},
                ]}}, y=3),
             _out("return { y: $json.y, has_x: 'x' in $json };")],  # has_x 区分替换/合并语义（P2-N4）
            [("Seed", "S", 0, 0), ("S", OUT_NODE, 0, 0)],
        ),
        "expect": {"y": 5, "has_x": False},  # 默认替换：x 被丢弃（has_x=False）；若为合并语义 has_x=True -> 断言 FAIL（P2-N4）
    })

    # 2) IF 多输出：true 分支
    s.append({
        "name": "if_true_branch",
        "wf": _finalize(
            [_seed("return { x: 5 };"),
             _node("IF", "n8n-nodes-base.if", 2, {"conditions": {
                 "options": {"caseSensitive": True},
                 "conditions": [{"leftValue": "={{ $json.x }}", "rightValue": 3,
                                 "operator": {"type": "number", "operation": "gt"}}]}}, y=3),
             _node("OutT", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                      "jsCode": "return { branch: 'true' };"}),
             _node("OutF", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                      "jsCode": "return { branch: 'false' };"}),
             ],
            [("Seed", "IF", 0, 0), ("IF", "OutT", 0, 0), ("IF", "OutF", 1, 0)],
        ),
        "expect_node": "OutT", "expect": {"branch": "true"},
    })

    # 3) Switch 多路由：命中第 2 条
    s.append({
        "name": "switch_routes",
        "wf": _finalize(
            [_seed("return { v: 'b' };"),
             _node("SW", "n8n-nodes-base.switch", 3, {"rules": {"values": [
                 {"conditions": {"options": {"caseSensitive": True}, "combinator": "and",
                  "conditions": [{"leftValue": "={{ $json.v }}", "rightValue": "a",
                                  "operator": {"type": "string", "operation": "equals"}}]}},
                 {"conditions": {"options": {"caseSensitive": True}, "combinator": "and",
                  "conditions": [{"leftValue": "={{ $json.v }}", "rightValue": "b",
                                  "operator": {"type": "string", "operation": "equals"}}]}},
                 {"conditions": {"options": {"caseSensitive": True}, "combinator": "and",
                  "conditions": [{"leftValue": "={{ $json.v }}", "rightValue": "c",
                                  "operator": {"type": "string", "operation": "equals"}}]}},
             ]}}, y=3),
             _node("OutA", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                      "jsCode": "return { route: 'a' };"}),
             _node("OutB", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                      "jsCode": "return { route: 'b' };"}),
             _node("OutC", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                      "jsCode": "return { route: 'c' };"}),
             ],
            [("Seed", "SW", 0, 0), ("SW", "OutA", 0, 0), ("SW", "OutB", 1, 0), ("SW", "OutC", 2, 0)],
        ),
        "expect_node": "OutB", "expect": {"route": "b"},
    })

    # 4) Switch fallback 'extra'：未匹配走 fallback 端口
    s.append({
        "name": "switch_fallback",
        "wf": _finalize(
            [_seed("return { v: 'z' };"),
             _node("SW", "n8n-nodes-base.switch", 3, {
                 "rules": {"values": [
                     {"conditions": {"options": {"caseSensitive": True}, "combinator": "and",
                      "conditions": [{"leftValue": "={{ $json.v }}", "rightValue": "a",
                                      "operator": {"type": "string", "operation": "equals"}}]}},
                     {"conditions": {"options": {"caseSensitive": True}, "combinator": "and",
                      "conditions": [{"leftValue": "={{ $json.v }}", "rightValue": "b",
                                      "operator": {"type": "string", "operation": "equals"}}]}},
                 ]},
                 "options": {"fallbackOutput": "extra"},
             }, y=3),
             _node("OutM", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                      "jsCode": "return { route: 'match' };"}),
             _node("OutF", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                      "jsCode": "return { route: 'fallback' };"}),
             ],
            [("Seed", "SW", 0, 0), ("SW", "OutM", 0, 0), ("SW", "OutF", 2, 0)],
        ),
        # fallback 端口 = 规则数之后的 extra 端口（2 规则 -> main[2]，
        # SwitchV3.node.ts:37-42）；下方 OutF 连 main[2] 即为未匹配分支
        "expect_node": "OutF", "expect": {"route": "fallback"},
        "note": "fallback 输出端口 = 规则数之后的 extra 端口（此处 main[2]），本场景验证未匹配数据路由到 fallback",
    })

    # 5) Merge 多输入合并
    s.append({
        "name": "merge_combine",
        "wf": _finalize(
            [_seed("return { seed: true };"),
             _node("A", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                   "jsCode": "return { a: 1 };"}),
             _node("B", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                   "jsCode": "return { b: 2 };"}),
             _node("M", "n8n-nodes-base.merge", 3, {"mode": "combine",
                                                    "combineBy": "combineAll"}),
             _out("return { a: $json.a, b: $json.b };")],
            [("Seed", "A", 0, 0), ("Seed", "B", 0, 0),
             ("A", "M", 0, 0), ("B", "M", 0, 1), ("M", OUT_NODE, 0, 0)],
        ),
        "expect": {"a": 1, "b": 2},
    })

    # 6) 表达式插值到字符串（Set + 拼接）
    s.append({
        "name": "expr_interpolation",
        "wf": _finalize(
            [_seed("return { name: 'n8n' };"),
             _node("S", "n8n-nodes-base.set", 3.5, {"assignments": {"assignments": [
                 {"name": "greeting", "value": "={{ $json.name }}!", "type": "string"},
             ]}}, y=3),
             _out("return { g: $json.greeting };")],
            [("Seed", "S", 0, 0), ("S", OUT_NODE, 0, 0)],
        ),
        "expect": {"g": "n8n!"},
    })

    # 7) Code 链（下游读上游输出）
    s.append({
        "name": "code_chain",
        "wf": _finalize(
            [_seed("return { x: 5 };"),
             _node("T", "n8n-nodes-base.code", 2, {"mode": "runOnceForAllItems",
                                                   "jsCode": "return { x: $json.x * 2 };"}),
             _out("return { x: $json.x };")],
            [("Seed", "T", 0, 0), ("T", OUT_NODE, 0, 0)],
        ),
        "expect": {"x": 10},
    })
    return s


# ---------------------------------------------------------------------------
# 命令实现
# ---------------------------------------------------------------------------

def _compile_scenario(scenario: dict, workflow_id: str) -> dict:
    ir = compile_ast(parse_workflow(scenario["wf"]), workflow_id=workflow_id,
                     version="1").to_dict()
    return decompile_to_workflow(ir, name=scenario["name"])


def command_build(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for scenario in _scenarios():
        wf = _compile_scenario(scenario, f"mx-{scenario['name']}")
        (out_dir / f"{scenario['name']}.json").write_text(
            json.dumps(wf, indent=2, ensure_ascii=False), encoding="utf-8")
        manifest.append({"name": scenario["name"], "id": wf["id"],
                         "nodes": [n["name"] for n in wf["nodes"]]})
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"构建完成: {len(manifest)} 场景 -> {out_dir}")
    for item in manifest:
        print(f"  {item['name']}: id={item['id']}")
    return 0


def command_remote_cmd(out_dir: Path) -> int:
    print(f"""远端执行（产物先拷到目标机 {out_dir}，再在该机跑）：

  # 目标机（有 docker + n8nio/n8n 镜像）：
  rm -rf /tmp/mx-data && mkdir -p /tmp/mx-data/out && chmod 777 /tmp/mx-data/out
  docker run --rm --entrypoint sh -v {out_dir}:/data -v /tmp/mx-data:/home/node/.n8n \\
    -e N8N_DIAGNOSTICS_ENABLED=false n8nio/n8n -c '
    for f in /data/*.json; do
      b=$(basename "$f" .json); [ "$b" = manifest ] && continue
      n8n import:workflow --input="$f" >/dev/null 2>&1
      n8n execute --id="mx-$b" --rawOutput > /home/node/.n8n/out/"$b".json 2>/dev/null
    done
    echo DONE'
  # 拉回: scp -r <remote>:/tmp/mx-data/out/ <local_dir>/
""")
    return 0


def _out_items(run_data: dict, node: str = OUT_NODE) -> list[dict]:
    return [item["json"] for item in run_data[node][0]["data"]["main"][0]]


def command_assert(results_dir: Path) -> int:
    failures = 0
    for scenario in _scenarios():
        result_path = results_dir / f"{scenario['name']}.json"
        if not result_path.exists():
            print(f"  MISSING {scenario['name']}: 无执行结果")
            failures += 1
            continue
        text = result_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        start = next(
            (i for i, line in enumerate(lines) if line.lstrip().startswith("{")), None)
        if start is None:
            print(f"  FAIL {scenario['name']}: 输出无 JSON（仅日志）")
            failures += 1
            continue
        raw = json.loads("\n".join(lines[start:]))
        status = raw.get("status")
        run_data = raw.get("data", {}).get("resultData", {}).get("runData", {})
        expect_node = scenario.get("expect_node", OUT_NODE)
        if status != "success":
            print(f"  FAIL {scenario['name']}: status={status!r}")
            failures += 1
            continue
        try:
            items = _out_items(run_data, expect_node)
        except (KeyError, IndexError, TypeError) as exc:
            print(f"  FAIL {scenario['name']}: 无输出数据 ({exc})")
            failures += 1
            continue
        got = items[0]
        if got == scenario["expect"]:
            print(f"  PASS {scenario['name']}: {got}")
        else:
            print(f"  FAIL {scenario['name']}: got {got} != expect {scenario['expect']}")
            failures += 1
    print(f"\n结果: {len(_scenarios()) - failures}/{len(_scenarios())} 通过")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="A2 矩阵化：多场景真实执行验证")
    ap.add_argument("action", choices=["build", "remote-cmd", "assert"])
    ap.add_argument("path", nargs="?", default=None)
    args = ap.parse_args()
    if args.action == "build":
        return command_build(Path(args.path or "/tmp/mx-out"))
    if args.action == "remote-cmd":
        return command_remote_cmd(Path(args.path or "/tmp/mx-out"))
    if args.action == "assert":
        return command_assert(Path(args.path or "/tmp/mx-out"))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
