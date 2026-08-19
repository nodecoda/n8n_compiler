"""IR -> n8n 实例部署 adapter（runtime/ 第二个组件）。

把编译器产出的 typed IR v1 反编译为 n8n Workflow JSON，并通过 n8n REST API
（POST /api/v1/workflows，X-N8N-API-KEY 认证）部署到真实实例。纯标准库
（urllib），零第三方依赖。

部署链：typed IR -> decompile -> n8n Workflow JSON -> POST /api/v1/workflows

A2 已验证 CLI import 路径（n8n import:workflow）；REST API 是等价的服务化
路径，供部署方以代码驱动（AI 生成 -> 编译 -> 部署闭环的落点）。

错误纪律（与外部 gate 同纪律）：
  - 输入先过 validate_typed_ir（digest 防篡改，decompile 入口强制）
  - HTTP 非 2xx / 网络失败 / 非 JSON 响应 -> 显式 ValueError（含状态码与
    响应体摘要），不静默降级
  - API key 只进请求头，不进 IR、不进日志

P2-3 凭据解析（跨实例部署）：
  - 节点引用的凭据按 name 解析为目标实例 id（GET /credentials 建映射），
    避免 n8n create 静默置空未知引用（replaceInvalidCredentials）导致
    运行时认证失败
  - 目标实例缺失的凭据 -> 部署前显式失败并列缺失清单（不静默降级）
  - credential_map 可显式提供 name->id 映射（跳过 GET，调用方自行管理）
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from runtime.decompile import decompile_to_workflow


def deploy_to_n8n(
    ir: dict[str, Any],
    *,
    base_url: str,
    api_key: str,
    name: str | None = None,
    timeout: float = 30.0,
    mode: str = "create",
    credential_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """部署 IR 文档到 n8n 实例，返回 n8n 创建的 workflow 对象（含服务端 id）。

    ``base_url``：n8n 实例地址（不含 /api 后缀），如 https://n8n.example.com。
    ``ir`` 入口过 validate_typed_ir（digest 校验默认开启），篡改/损坏显式失败。
    ``mode``：
      - "create"（默认）：恒 POST 新建（P2-2 前语义，幂等性由调用方保证）；
      - "upsert"：GET ?name= 命中则 PUT 更新（n8n update 路由为 PUT，
        workflows.id.yml；PATCH -> 405），未命中则 POST 新建——同一 IR 重复
        部署不再产生重复工作流。需 key 同时具备 workflow:create + workflow:list
        + workflow:update（list 是 GET /workflows 的 scope，不是 read）。
    """
    workflow = decompile_to_workflow(
        ir,
        name=name or ir.get("workflow", {}).get("id") or "decompiled",
    )
    # REST API 与 CLI import 的契约差异在此收口：workflowCreate schema 把 id
    # 标为 readOnly（携带 -> 400 "request/body/id is read-only"），服务端生成
    # 新 id；而 CLI import 路径需要 id（workflow_entity.id）。部署前剥掉 id。
    workflow.pop("id", None)
    if mode not in ("create", "upsert"):
        raise ValueError(f"n8n deploy failed: unknown mode {mode!r}")
    endpoint = base_url.rstrip("/") + "/api/v1/workflows"
    _resolve_credentials(workflow, base_url, api_key, timeout=timeout,
                         credential_map=credential_map)

    if mode == "upsert":
        existing = _find_workflow_by_name(endpoint, api_key, workflow["name"],
                                          timeout=timeout)
        if existing is not None:
            return _request_json(
                f"{endpoint}/{existing}",
                method="PUT", body=workflow, api_key=api_key, timeout=timeout,
                phase="upsert PUT",
            )
    return _request_json(
        endpoint, method="POST", body=workflow, api_key=api_key, timeout=timeout,
        phase="create POST",
    )


def _list_credentials(
    base_url: str, api_key: str, *, timeout: float
) -> list[dict[str, Any]]:
    """GET /api/v1/credentials 全量拉取（cursor 分页遍历至 nextCursor null）。

    ``base_url`` 为实例根地址（不含 /api 后缀）；credentials 端点是独立资源，
    不能从 workflows endpoint 推导（/api/v1/workflows + /credentials 是 404）。
    该端点分页只接受 limit/cursor（offset -> 400 "Unknown query parameter"）。
    """
    import urllib.parse
    creds: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        url = base_url.rstrip("/") + "/api/v1/credentials" + "?limit=250"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)
        payload = _request_json(
            url, method="GET", body=None, api_key=api_key, timeout=timeout,
            phase="credentials list",
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            break
        creds.extend(item for item in data if isinstance(item, dict))
        cursor = payload.get("nextCursor")
        if not cursor:
            break
    return creds


def _resolve_credentials(
    workflow: dict[str, Any],
    base_url: str,
    api_key: str,
    *,
    timeout: float,
    credential_map: dict[str, str] | None = None,
) -> None:
    """把节点凭据引用按 name 解析为目标实例 id（P2-3）。

    - 无凭据引用 -> 零请求（矩阵场景不含凭据，不引入额外 GET）
    - 引用 {type: {name, id}} 中 name 缺失时退用 id 作为匹配键
    - 目标实例缺失的 name -> 部署前 ValueError 列缺失清单（不静默降级）
    - credential_map 提供 name->id 时跳过 GET（调用方自行管理映射）
    """
    refs: dict[str, dict[str, Any]] = {}
    for node in workflow.get("nodes", []):
        creds = node.get("credentials") or {}
        for cred_type, spec in creds.items():
            if not isinstance(spec, dict):
                continue
            ref_name = spec.get("name") or spec.get("id") or ""
            if not ref_name:
                continue
            info = refs.setdefault(ref_name, {"types": set(), "nodes": []})
            info["types"].add(cred_type)
            info["nodes"].append((node, cred_type))
    if not refs:
        return

    resolved: dict[str, str] = dict(credential_map or {})
    if not resolved:
        for cred in _list_credentials(base_url, api_key, timeout=timeout):
            resolved.setdefault(cred.get("name") or "", cred.get("id") or "")

    missing = sorted(name for name in refs if name not in resolved)
    if missing:
        raise ValueError(
            "n8n deploy failed: credentials missing on target instance: "
            f"{missing}（先在目标实例创建，或传 credential_map 映射）"
        )
    for name, info in refs.items():
        target_id = resolved[name]
        for node, cred_type in info["nodes"]:
            node["credentials"][cred_type] = {
                **node["credentials"][cred_type], "name": name, "id": target_id,
            }


def _find_workflow_by_name(
    endpoint: str, api_key: str, name: str, *, timeout: float
) -> str | None:
    """GET /api/v1/workflows?name=… 命中返回首个 workflow id，未命中 None。"""
    import urllib.parse
    url = endpoint + "?" + urllib.parse.urlencode({"name": name})
    payload = _request_json(url, method="GET", body=None, api_key=api_key,
                            timeout=timeout, phase="lookup GET")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    return first.get("id") if isinstance(first, dict) else None


def _request_json(
    url: str, *, method: str, body: dict[str, Any] | None, api_key: str,
    timeout: float, phase: str,
) -> dict[str, Any]:
    """统一 REST 请求：JSON 编码 -> 发送 -> 显式失败 -> 响应校验（P3-3）。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"X-N8N-API-KEY": api_key}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ValueError(
            f"n8n deploy failed ({phase}): HTTP {exc.code} {exc.reason}: {detail}"
        ) from exc
    except OSError as exc:
        raise ValueError(f"n8n deploy failed ({phase}): network error: {exc}") from exc
    try:
        created = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"n8n deploy failed ({phase}): non-JSON response: {payload[:200]!r}"
        ) from exc
    if method != "GET" and not isinstance(created, dict):
        raise ValueError(f"n8n deploy failed ({phase}): non-object response")
    if method != "GET" and not created.get("id"):
        # P3-3：2xx 但响应非 workflow（无 id）显式失败，不静默返回
        raise ValueError(
            f"n8n deploy failed ({phase}): response missing workflow id: "
            f"{str(created)[:200]}"
        )
    return created


def deploy_ir_json(
    payload: str | bytes | bytearray,
    *,
    base_url: str,
    api_key: str,
    name: str | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """部署 IR JSON 文本到 n8n 实例（严格装载；deploy_to_n8n 内过 digest）。"""
    try:
        ir = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid typed IR JSON: {exc}") from exc
    if not isinstance(ir, dict):
        raise ValueError("typed IR document must be a JSON object")
    return deploy_to_n8n(ir, base_url=base_url, api_key=api_key,
                         name=name, timeout=timeout)
