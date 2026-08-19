"""共享测试基础设施 — 单一来源。

职责：
  1. fixture 工厂（节点/工作流构造），消除测试文件间重复与跨模块 import
  2. 外部依赖解析（N8N_REPO 环境变量优先，默认 /home/dev/n8n）
  3. 分层 skip 守卫（unit / integration(node) / matrix(n8n repo)）

规则（见 TESTING.md）：
  - 测试文件禁止直接 import 其它测试模块（用 helpers）
  - 禁止硬编码 /home/dev/n8n（用 n8n_repo()）
"""
from __future__ import annotations

import os
import shutil
import unittest
from pathlib import Path
from typing import Any, Callable, TypeVar

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_N8N_REPO = Path("/home/dev/n8n")
RAG_FIXTURE_REL = Path("packages/@n8n/workflow-sdk/test-fixtures/committed-workflows/6.json")
COMMITTED_DIR_REL = Path("packages/@n8n/workflow-sdk/test-fixtures/committed-workflows")
PLAYWRIGHT_DIR_REL = Path("packages/testing/playwright/workflows")
TEMPLATES_DIR_REL = Path("packages/frontend/editor-ui/src/features/workflows/templates/utils/samples")


def n8n_repo() -> Path:
    """n8n 仓库根（N8N_REPO 环境变量优先；默认开发机路径）。"""
    env = os.environ.get("N8N_REPO")
    if env:
        return Path(env)
    return DEFAULT_N8N_REPO


def rag_fixture() -> Path:
    """RAG 端到端 fixture 路径。"""
    return n8n_repo() / RAG_FIXTURE_REL


def require_n8n_repo(test_item: Callable[..., Any]) -> Callable[..., Any]:
    """矩阵/端到端测试守卫：n8n 仓库缺失时 skip。"""
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not n8n_repo().exists():
            raise unittest.SkipTest(f"n8n repo not found at {n8n_repo()} (set N8N_REPO)")
        return test_item(*args, **kwargs)
    wrapper.__name__ = getattr(test_item, "__name__", "wrapper")
    wrapper.__doc__ = getattr(test_item, "__doc__", None)
    return wrapper


def skip_unless_node(cls: type) -> type:
    """类级守卫：所有用例都需要 Node/acorn 桥（与 code/js_parser.find_node 同源）。

    缺 Node 时按层 skip，不伪造结果（与 n8n-repo 守卫同纪律；覆盖 TESTING.md
    「缺依赖时按层 skip」的 node 层）。
    """
    from code.js_parser import JSInfraError, find_node

    original = cls.setUp

    def setUp(self: Any) -> None:
        try:
            node = find_node()
            # find_node 对 env 路径不校验可执行性（真实失败发生在桥进程）；
            # 守卫与真实失败点对齐：解析后仍不可执行 -> skip
            resolved = shutil.which(node) or node
            if not os.access(resolved, os.X_OK):
                raise unittest.SkipTest(f"Node unavailable: {resolved} 不可执行")
        except JSInfraError as exc:
            raise unittest.SkipTest(f"Node unavailable: {exc}") from exc
        original(self)

    cls.setUp = setUp
    return cls


def skip_unless_n8n_repo(cls: type) -> type:
    """类级守卫：所有用例都需要 n8n 仓库。"""
    original = cls.setUp

    def setUp(self: Any) -> None:
        if not n8n_repo().exists():
            raise unittest.SkipTest(f"n8n repo not found at {n8n_repo()} (set N8N_REPO)")
        original(self)

    cls.setUp = setUp
    return cls


# ---------------------------------------------------------------------------
# fixture 工厂
# ---------------------------------------------------------------------------


def webhook_node(name: str = "Webhook", **params: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": name,
        "type": "n8n-nodes-base.webhook",
        "typeVersion": 2,
        "position": [0, 0],
        "parameters": {"path": "in", "httpMethod": "POST"},
    }
    node["parameters"].update(params)
    return node


def langchain_code_node(name: str, js: str) -> dict[str, Any]:
    """AI 链内联 Code 节点（@n8n/n8n-nodes-langchain.code）。

    参数形状对齐 n8n 真实导出：parameters.code.supplyData.code 承载工厂源码
    （supplyData 固定集合包装），outputs.output[].type 声明 ai_* 输出。
    """
    return {
        "name": name,
        "type": "@n8n/n8n-nodes-langchain.code",
        "typeVersion": 1,
        "position": [0, 0],
        "parameters": {
            "code": {"supplyData": {"code": js}},
            "outputs": {"output": [{"type": "ai_embedding"}]},
        },
    }


def langchain_code_execute_node(name: str, js: str) -> dict[str, Any]:
    """langchain.code execute 变体（Main 输出）：parameters.code.execute.code。

    P2-1（v5）：n8n 双模式之一——execute 用 runCodeAllItems（items 全量数组），
    与 supplyData 工厂（无 items）语义不同。
    """
    return {
        "name": name,
        "type": "@n8n/n8n-nodes-langchain.code",
        "typeVersion": 1,
        "position": [0, 0],
        "parameters": {
            "code": {"execute": {"code": js}},
        },
    }


def tool_code_node(name: str, js: str, *, language: str = "javaScript") -> dict[str, Any]:
    """AI 链 Code Tool 子节点（@n8n/n8n-nodes-langchain.toolCode）。

    P2-4（v5）：n8n 形状——顶层 jsCode/pythonCode（非 supplyData 嵌套），
    language 分流；0 main 入/出，输出仅 ai_tool 子连接。
    """
    params: dict[str, Any] = {"language": language}
    if language == "javaScript":
        params["jsCode"] = js
    else:
        params["pythonCode"] = js
    return {
        "name": name,
        "type": "@n8n/n8n-nodes-langchain.toolCode",
        "typeVersion": 1,
        "position": [0, 0],
        "parameters": params,
    }


def manual_trigger_node(name: str = "Trigger") -> dict[str, Any]:
    return {
        "name": name,
        "type": "n8n-nodes-base.manualTrigger",
        "typeVersion": 1,
        "position": [0, 0],
        "parameters": {},
    }


def set_node(name: str, *, x: int = 0, **params: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": name,
        "type": "n8n-nodes-base.set",
        # P3-6：assignments 形状要求 typeVersion >= 3.3（SetV2 按版本分流，
        # <3.3 走旧 fields.values 并静默忽略 assignments）；3.5 = 当前默认版
        "typeVersion": 3.5,
        "position": [x, 2],
        "parameters": {"assignments": {"assignments": []}},
    }
    node["parameters"].update(params)
    return node


def code_node(name: str, source: str, *, x: int = 0, mode: str = "runOnceForAllItems") -> dict[str, Any]:
    return {
        "name": name,
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [x, 2],
        "parameters": {"mode": mode, "jsCode": source},
    }


def if_node(name: str = "IF", *, x: int = 0, **params: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": name,
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": [x, 1],
        "parameters": {"conditions": {"options": {}}},
    }
    node["parameters"].update(params)
    return node


def switch_node(name: str, routes: int, *, x: int = 1) -> dict[str, Any]:
    return {
        "name": name,
        "type": "n8n-nodes-base.switch",
        "typeVersion": 2,
        "position": [x, 1],
        "parameters": {"rules": {"values": [{} for _ in range(routes)]}},
    }


def limit_node(name: str, *, x: int = 0, **params: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "name": name,
        "type": "n8n-nodes-base.limit",
        "typeVersion": 1,
        "position": [x, 2],
        "parameters": {"maxItems": 1},
    }
    node["parameters"].update(params)
    return node


def make_workflow(nodes: list[dict[str, Any]], connections: dict[str, Any]) -> dict[str, Any]:
    """n8n workflow JSON 构造器。"""
    return {
        "name": "test-workflow",
        "nodes": nodes,
        "connections": connections,
    }


def mini_webhook_workflow(**overrides: Any) -> dict[str, Any]:
    """最小合法工作流：webhook -> IF -> Set(true 分支)。"""
    wf = make_workflow(
        [webhook_node("Webhook"), if_node("IF"), set_node("Yes Branch")],
        {
            "Webhook": {"main": [[{"node": "IF"}]]},
            "IF": {"main": [[{"node": "Yes Branch"}]]},  # IF 单连 true（n8n 合法）
        },
    )
    wf.update(overrides)
    return wf


def switch_workflow(routes: int, **conn_overrides: Any) -> dict[str, Any]:
    """webhook -> Switch(routes 路) -> 每路一个 Set。"""
    wf = make_workflow(
        [webhook_node("Webhook"), switch_node("Switch", routes)]
        + [set_node(f"Target{i}", x=i) for i in range(routes)],
        {
            "Webhook": {"main": [[{"node": "Switch"}]]},
            "Switch": {"main": [[{"node": f"Target{i}"}] for i in range(routes)]},
        },
    )
    for key, value in conn_overrides.items():
        wf["connections"][key] = value
    return wf


def chain_workflow(nodes: list[dict[str, Any]], links: list[tuple[str, str]]) -> dict[str, Any]:
    """链式工作流：nodes + 显式 main 连线。"""
    connections: dict[str, Any] = {}
    for source, target in links:
        connections.setdefault(source, {"main": []})["main"].append([{"node": target}])
    return make_workflow(nodes, connections)
