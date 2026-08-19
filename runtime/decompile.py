"""typed IR -> n8n Workflow JSON 反编译器（runtime/ 第一个组件）。

P3-3（v5）：IR 版本口径收口——当前编译产物为 v2（connections 携带
conn_type，ai_* 子连接完整还原）；旧 v1 文档（无 conn_type/settings）
仍可装载，settings 缺失回退编辑器默认 v1。

把编译器产出的 IR 文档还原为 n8n 可导入的工作流 JSON（nodes + connections），
与 parser 形成 round-trip 闭环：n8n JSON -> parse -> compile -> IR -> 本模块
-> n8n JSON。验证编译无损，也为部署适配器（把 IR 部署回 n8n）打基础。

n8n 适配点：
  - 剔除合成节点：synthetic.exit 的 __exit__（编译器内部收口，非 n8n 原生
    节点；synthetic.entry 当前不产生，防御性一并剔除）。
  - IR connections（from_port: "main"|"main_N"，to_index = 目标输入端口，
    conn_type: main/ai_*）-> n8n connections[源][conn_type][端口索引] =
    [{node, type: conn_type, index}]。ai_* 子连接（v2 携带）原样还原，
    方向与 n8n 一致（子节点 -> 主节点）。
  - IR workflow {id, version, entry_keys, exit_key, settings}：id 还原到产物
    （缺失/为空时生成 UUID——n8n 导入要求 workflow_entity.id 非空且唯一，
    真实执行验证抓出的缺口）；name 由调用方传入；settings 自 IR v2 携带并
    还原（缺失回退编辑器默认 v1——REST schema 强制字段存在）；pinData 未入
    IR 不还原——本模块保证编译语义往返，不保证完整审计往返。

错误策略：输入先过 typed_ir 严格校验（白名单 + digest），篡改/损坏的 IR
显式失败，不静默产出半成品工作流（与外部 gate 同纪律）。
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from ast_nodes.node_type import EXIT_NODE_KEY
from typed_ir import validate_typed_ir

_SYNTHETIC_TYPES = {"synthetic.entry", "synthetic.exit"}
_PORT_RE = re.compile(r"^main_(\d+)$")


def _port_index(from_port: str) -> int:
    """IR from_port -> n8n 输出端口索引（"main" -> 0，"main_N" -> N）。"""
    if from_port == "main":
        return 0
    m = _PORT_RE.match(from_port)
    if m:
        return int(m.group(1))
    return 0  # 宽容：畸形端口名按 0（typed_ir 校验已挡在入口）


def _num(value: Any) -> Any:
    """IR 数字 -> n8n 原生形状：整值浮点归一为 int（JSON 数值语义不变）。

    n8n 导出中 typeVersion/position 通常是整数；IR 内部统一 float，
    反编译时整值浮点归回 int，避免导出 JSON 出现 2.0 / [0.0, 0.0]。
    """
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _restore_error_policy(result: dict[str, Any], ep: dict[str, Any]) -> None:
    """IR error_policy -> n8n 节点顶层 onError/retry 字段。

    只在非默认值时回写（n8n 默认 stopWorkflow / 无重试，写了是噪音；
    IR 里 N8NErrorPolicy 也是同样默认）。部署 adapter 需此还原（P2-13）。
    """
    if ep.get("on_error") not in (None, "stopWorkflow"):
        result["onError"] = ep["on_error"]
    if ep.get("retry_on_fail"):
        result["retryOnFail"] = True
        if ep.get("max_tries") is not None:
            result["maxTries"] = ep["max_tries"]
        if ep.get("wait_between_tries") is not None:
            result["waitBetweenTries"] = ep["wait_between_tries"]


def _node_to_n8n(node: dict[str, Any]) -> dict[str, Any]:
    """IR node dict -> n8n node dict（剔除编译器内部字段）。"""
    config = node["config"]
    result: dict[str, Any] = {
        "name": node["name"],
        "type": config["n8n_type"],
        "typeVersion": _num(config.get("type_version", 1)),
        "position": [_num(x) for x in config.get("position", [0, 0])],
        # n8n 导出中 parameters 恒存在（空参数也是 {}），保持原生形状
        "parameters": config.get("parameters") or {},
    }
    credentials = config.get("credentials")
    if credentials:
        result["credentials"] = credentials  # 凭据引用（id/name），无敏感值
    _restore_error_policy(result, config.get("error_policy") or {})
    return result


def decompile_to_workflow(ir: dict[str, Any], *, name: str = "decompiled") -> dict[str, Any]:
    """IR 文档 -> n8n Workflow JSON dict（nodes + connections）。

    ``ir`` 必须是严格校验通过的 typed IR v1 文档（validate_typed_ir 在入口
    执行；digest 校验默认开启，防篡改/损坏）。
    """
    validate_typed_ir(ir)

    nodes = [
        _node_to_n8n(node)
        for node in ir["nodes"]
        if node["config"].get("n8n_type") not in _SYNTHETIC_TYPES
    ]

    synthetic_keys = {
        node["key"]
        for node in ir["nodes"]
        if node["config"].get("n8n_type") in _SYNTHETIC_TYPES
    }
    connections: dict[str, dict[str, Any]] = {}
    for conn in ir["connections"]:
        # P2-14：合成节点出边（如 synthetic.entry 的发射边）与 exit 收口边一并
        # 剔除——只剔 to_node 会留下 from_node 已随节点剔除的悬空连接
        if conn["to_node"] == EXIT_NODE_KEY or conn["from_node"] in synthetic_keys:
            continue
        conn_type = conn.get("conn_type", "main")  # v2 显式 / v1 缺省 main
        port_index = _port_index(conn["from_port"])
        # P1-1c（v4）：按 conn_type 分组还原——main 边回 connections[src]["main"]，
        # ai_* 子连接（方向：子节点 -> 主节点）回 connections[src][conn_type]，
        # 边 type 与 n8n 原生一致（main / ai_embedding / ai_tool ...）。
        type_ports = connections.setdefault(conn["from_node"], {}).setdefault(conn_type, [])
        while len(type_ports) <= port_index:
            type_ports.append([])
        type_ports[port_index].append({
            "node": conn["to_node"],
            "type": conn_type,
            "index": conn.get("to_index", 0),  # 缺省 = 单输入（typed_ir 白名单可选）
        })

    return {
        "id": ir["workflow"].get("id") or str(uuid.uuid4()),
        "name": name,
        "nodes": nodes,
        "connections": connections,
        # n8n REST API（POST /api/v1/workflows）强制要求 settings 字段存在
        # （真实实例验证抓出的缺口：缺失 settings -> 400 schema 校验失败）。
        # settings（P3-8，v4）：IR v2 携带源 settings 原值则原样还原（v2 源工作流
        # 不再被强制降级 v1）；缺失回退编辑器默认 v1。注意：n8n CLI import 不填充
        # settings（import/workflow.ts 直存实体），运行时把缺失 executionOrder 当
        # v2 处理（execution-engine/workflow-execute.ts）——早期恒 v1 是刻意的
        # 确定性选择，v2 起由「源 settings 优先 + v1 兜底」取代，行为更忠实。
        "settings": ir.get("workflow", {}).get("settings") or {"executionOrder": "v1"},
    }


def decompile_ir_json(payload: str | bytes | bytearray, *, name: str = "decompiled") -> dict[str, Any]:
    """IR JSON 字符串 -> n8n Workflow JSON dict（严格装载 + digest 校验）。"""
    import json
    try:
        ir = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid typed IR JSON: {exc}") from exc
    if not isinstance(ir, dict):
        raise ValueError("typed IR document must be a JSON object")
    return decompile_to_workflow(ir, name=name)
