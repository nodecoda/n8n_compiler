#!/usr/bin/env python3
"""A2-REST：真实实例 REST 部署验证（runtime/deploy.py 路径）。

execute_matrix.py 验证 CLI import 路径（n8n import:workflow）；本脚本验证
等价的服务化路径：deploy_to_n8n（POST /api/v1/workflows + X-N8N-API-KEY）
把同一批矩阵场景部署到真实 n8n 实例，再 n8n execute --id=<服务端 id> 执行并
断言。两条路径产物一致、执行语义一致 -> REST 部署链闭合。

用法:
    # API key 走环境变量（AGENTS 秘密纪律：不进命令行）
    export N8N_API_KEY=n8n_api_xxx
    # 1) 部署：本地起 SSH 隧道（Popen 持柄，finally 必收）到远端的
    #    127.0.0.1:5678，逐场景 deploy_to_n8n，记录服务端生成的 workflow id；
    #    单场景失败不中止整批（P2-4），ids.json 始终落盘
    python3 scripts/execute_deploy.py deploy \
        --remote nodecoda-production --base-port 5678 -o /tmp/mx-deploy
    # 2) 远端执行：执行前清空 out_dir 防陈旧结果假 PASS（P2-5）
    python3 scripts/execute_deploy.py execute \
        --remote nodecoda-production --container n8n-deploy-test \
        -o /tmp/mx-deploy/out --ids /tmp/mx-deploy/ids.json
    # 3) 断言（复用 execute_matrix 的断言逻辑）
    python3 scripts/execute_matrix.py assert /tmp/mx-deploy/out
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))  # 复用 execute_matrix._scenarios

from compiler.workflow import compile_ast  # noqa: E402
from parser.workflow import parse_workflow  # noqa: E402
from runtime.deploy import deploy_to_n8n  # noqa: E402
from execute_matrix import _scenarios  # noqa: E402

_SID_RE = re.compile(r"^[A-Za-z0-9_-]+$")          # n8n 工作流 id（base58 风格）
_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")  # docker 容器名
_REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")


def _sh(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


def _wait_healthz(url: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                if resp.status == 200:
                    return
        except OSError:
            time.sleep(2)
    raise RuntimeError(f"instance not healthy at {url} after {timeout}s")


def _require_api_key(cli_value: str | None) -> str:
    """P3-4：API key 优先取 N8N_API_KEY 环境变量（ps 不可见），CLI 值仅应急。"""
    value = os.environ.get("N8N_API_KEY") or cli_value
    if not value:
        raise SystemExit("missing API key: set N8N_API_KEY or pass --api-key")
    return value


def command_deploy(args) -> int:
    """起隧道（Popen 持柄）-> 逐场景 deploy_to_n8n（per-scenario 容错）-> 落盘。"""
    out_dir = Path(args.output).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    api_key = _require_api_key(args.api_key)
    base_url = f"http://127.0.0.1:{args.base_port}"

    # P2-4：Popen 持柄替代 `ssh -f -N` + pkill substring（可误杀无关隧道）。
    tunnel = subprocess.Popen(
        ["ssh", "-N", "-L", f"{args.base_port}:127.0.0.1:{args.base_port}",
         args.remote],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # 隧道进程存活且健康检查通过才继续；否则快速失败（避免静等 60s）
        deadline = time.time() + 15
        while time.time() < deadline:
            if tunnel.poll() is not None:
                raise RuntimeError(f"ssh tunnel exited early (rc={tunnel.returncode})")
            time.sleep(0.5)
        _wait_healthz(base_url + "/healthz")

        ids: dict[str, dict] = {}
        failures = 0
        for scenario in _scenarios():
            try:
                wf = scenario["wf"]
                ir = compile_ast(parse_workflow(wf),
                                 workflow_id=f"mx-{scenario['name']}",
                                 version="1").to_dict()
                created = deploy_to_n8n(ir, base_url=base_url, api_key=api_key,
                                        name=scenario["name"], mode=args.mode)
                sid = created.get("id")
                if not sid:
                    raise ValueError(f"no id in response: {str(created)[:200]}")
                ids[scenario["name"]] = {
                    "server_id": sid,
                    "expect": scenario["expect"],
                    "expect_node": scenario.get("expect_node", "Out"),
                }
                print(f"  DEPLOYED {scenario['name']}: id={sid}")
            except Exception as exc:  # P2-4：单场景失败记录继续，不中止整批
                failures += 1
                print(f"  FAIL {scenario['name']}: {exc}")
        # P2-4：ids.json 始终落盘（含部分成功），幂等重试只补失败场景
        (out_dir / "ids.json").write_text(
            json.dumps(ids, indent=2, ensure_ascii=False), encoding="utf-8")
        ok = len(ids) - failures
        print(f"部署: {ok}/{len(ids)} 成功 -> {out_dir / 'ids.json'}"
              + ("" if not failures else f"（{failures} 失败，已记录）"))
        return 1 if failures else 0
    except Exception as exc:
        print(f"deploy aborted: {exc}")
        return 2
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except subprocess.TimeoutExpired:
            tunnel.kill()


def command_execute(args) -> int:
    """远端 docker exec n8n execute --id=<server_id> --rawOutput 拉回结果。"""
    ids_path = Path(args.ids)
    if not ids_path.exists():
        print(f"ids file not found: {ids_path}")
        return 2
    if not _CONTAINER_RE.fullmatch(args.container):
        print(f"invalid container name: {args.container!r}")
        return 2
    ids = json.loads(ids_path.read_text(encoding="utf-8"))
    out_dir = Path(args.output).resolve()
    # P2-5：执行前重建 out_dir，防止失败场景残留上一轮成功文件被 assert 误读
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    failures = 0
    for name, meta in ids.items():
        sid = meta["server_id"]
        if not _SID_RE.fullmatch(sid):  # P3-5：sid 内插远端 shell 前先校验
            print(f"  FAIL {name}: invalid server id {sid!r}")
            failures += 1
            continue
        # 容器内常驻 n8n 服务进程已占用 task broker 端口 5679；第二个进程
        # （CLI execute）须用独立 broker 端口，否则 "Task Broker's port 5679
        # is already in use" 直接退出（真实实例抓出的第 3 个坑）。
        cmd = (f"docker exec {args.container} sh -c \"N8N_RUNNERS_BROKER_PORT=15679 "
               f"n8n execute --id={sid} --rawOutput 2>/dev/null\"; "
               f"echo EXIT=$?")
        proc = _sh(["ssh", args.remote, cmd])
        stdout = proc.stdout
        marker = "EXIT="
        exit_code = None
        if marker in stdout:
            body, _, tail = stdout.rpartition(marker)
            exit_code = tail.strip()
            stdout = body
        if proc.returncode != 0 or exit_code != "0":
            print(f"  FAIL {name}: ssh/execute rc={proc.returncode} exit={exit_code} "
                  f"err={proc.stderr[:200]}")
            failures += 1
            continue
        (out_dir / f"{name}.json").write_text(stdout, encoding="utf-8")
        print(f"  EXECUTED {name}: {len(stdout)} bytes")
    print(f"\n执行: {len(ids) - failures}/{len(ids)} 成功 -> {out_dir}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="A2-REST：真实实例 REST 部署验证")
    sub = ap.add_subparsers(dest="action", required=True)

    d = sub.add_parser("deploy", help="起隧道并 deploy_to_n8n 全部矩阵场景")
    d.add_argument("--remote", default="nodecoda-production")
    d.add_argument("--base-port", type=int, default=5678)
    d.add_argument("--api-key", default=None,
                   help="应急用；推荐 export N8N_API_KEY（不进 ps）")
    d.add_argument("--mode", default="create", choices=["create", "upsert"],
                   help="create=恒 POST；upsert=按 name 查重 PATCH/POST（P2-2）")
    d.add_argument("-o", "--output", default="/tmp/mx-deploy")
    d.set_defaults(func=command_deploy)

    e = sub.add_parser("execute", help="远端 execute 已部署的工作流")
    e.add_argument("--remote", default="nodecoda-production")
    e.add_argument("--container", default="n8n-deploy-test")
    e.add_argument("--ids", default="/tmp/mx-deploy/ids.json")
    e.add_argument("-o", "--output", default="/tmp/mx-deploy/out")
    e.set_defaults(func=command_execute)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
