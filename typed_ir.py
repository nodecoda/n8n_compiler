"""n8n-typed-ir 装载与校验（v2 当前 / v1 兼容）— 对齐 coze_compiler.typed_ir 的严格程度。

execution_order 语义（P2-1）：字段名是历史继承（对齐 coze），实际值是**单
scope 的 Kahn 拓扑序**，不是 n8n 执行序——n8n 运行时按数据到达调度（逐 item、
Merge 等齐/到达语义）。消费方不得把 execution_order 当 n8n 执行序；若需 n8n
执行语义，须另行建模（如 settings.executionOrder 与数据驱动调度）。

白名单字段 + 严格类型装载 + SHA-256 digest。n8n 适配点：
  - 无 hierarchy（恒空 dict）、无 branches/generated_nodes/linked_workflows
  - exit_key 恒为合成 __exit__；entry_keys 为 trigger 节点列表（可多个）
  - 节点级 error_policy（N8NErrorPolicy）校验
  - Code 节点 config.js（contract）与 config.js_ast 可选携带
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ast_nodes.node_type import EXIT_NODE_KEY, ROOT_SCOPE

IR_FORMAT = "n8n-typed-ir"
# P1-1c（v4）：IR v2 = connections 携带 conn_type（ai_* 子连接完整还原）。
IR_VERSION = 2
# P2-2：accepted 集合（v1.x 兼容演进通道）。v2 起 ai 边进 connections；
# 旧 v1 文档（无 conn_type）仍可装载，conn_type 缺省 "main"。
_ACCEPTED_VERSIONS = frozenset({1, 2})

_TOP_LEVEL_FIELDS = {
    "format", "format_version", "workflow", "nodes", "connections",
    "hierarchy", "execution_order", "manifest", "digest",
}
_REQUIRED_TOP_LEVEL_FIELDS = _TOP_LEVEL_FIELDS - set()
# settings 可选（v2 携带源 settings 原值，v1 无此字段）：缺失 -> decompile 回退
# 编辑器默认 {"executionOrder": "v1"}（REST workflowCreate schema 强制 settings 存在）
_WORKFLOW_FIELDS = {"id", "version", "entry_keys", "exit_key", "settings"}
_NODE_FIELDS = {
    "key", "type", "name", "parent_key", "input_types", "output_types",
    "input_sources", "output_sources", "error_policy", "dependencies", "config",
}
_TYPE_FIELDS = {"type", "required", "desc", "elem_type_info", "properties"}
_FIELD_INFO_FIELDS = {"path", "source"}
_SOURCE_FIELDS = {"ref", "literal"}
_REF_FIELDS = {"from_node_key", "from_path", "variable_type"}
_ERROR_POLICY_FIELDS = {"on_error", "retry_on_fail", "max_tries", "wait_between_tries"}
_DEPENDENCY_FIELDS = {"direct", "indirect", "parent", "static_values", "variables"}
_MAPPING_FIELDS = {"from_path", "to_path"}
_STATIC_VALUE_FIELDS = {"path", "value"}
_VARIABLE_FIELDS = {"variable_type", "from_path", "to_path"}
_CONFIG_FIELDS = {
    "kind", "n8n_type", "type_version", "position", "parameters",
    "error_policy", "credentials", "js", "js_ast",
}
_CONNECTION_FIELDS = {"from_node", "from_port", "to_node", "to_port"}
# to_index（目标输入端口）可选：缺失 = 单输入（to_index 0），兼容旧 IR v1；
# conn_type 可选：缺省 "main"（v1 文档无此字段，v2 显式携带）
_CONNECTION_ALLOWED_FIELDS = _CONNECTION_FIELDS | {"to_index", "conn_type"}

_DATA_TYPES = {"any", "string", "number", "boolean", "list", "object", "binary"}
_GLOBAL_VARIABLE_TYPES = {"env", "execution", "workflow", "now", "parameters", "items"}
_ERROR_POLICY_VALUES = {"stopWorkflow", "continueRegularOutput", "continueErrorOutput"}
_BRANCH_PORT = re.compile(r"^main(_[0-9]+)?$")


def reject_non_finite(value: str) -> Any:
    """JSON 非有限常量（NaN/Infinity）显式拒绝。

    P2-9：digest 侧已 allow_nan=False，输入侧前置报错更早更清晰——NaN 进
    IR 会让序列化在编译后期才炸，错误信息误导排障。公开导出（P3-5）供
    cli.py 等跨模块使用，替代导入私有符号。
    """
    raise ValueError(f"JSON contains non-finite constant {value!r}")


def compute_typed_ir_digest(document: dict[str, Any]) -> str:
    """对除 digest 外全部语义字段计算 SHA-256。"""
    body = {key: value for key, value in document.items() if key != "digest"}
    canonical = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_typed_ir_digest(document: dict[str, Any]) -> None:
    """校验 digest 一致性。"""
    expected = document.get("digest")
    if not isinstance(expected, str) or not expected.startswith("sha256:"):
        raise ValueError("typed IR digest must be a sha256 string")
    actual = compute_typed_ir_digest(document)
    if actual != expected:
        raise ValueError(f"typed IR digest mismatch: expected {expected}, got {actual}")


def load_typed_ir_json(payload: str | bytes | bytearray) -> dict[str, Any]:
    """加载并严格校验 typed IR JSON。"""
    try:
        value = json.loads(payload, parse_constant=reject_non_finite)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid typed IR JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("typed IR document must be a JSON object")
    validate_typed_ir(value)
    return value


def validate_typed_ir(document: dict[str, Any], *, verify_digest: bool = True) -> None:
    """严格校验 n8n-typed-ir v1 文档。"""
    if not isinstance(document, dict):
        raise ValueError("typed IR document must be an object")
    _validate_fields(document, required=_REQUIRED_TOP_LEVEL_FIELDS,
                     allowed=_TOP_LEVEL_FIELDS, path="typed IR")
    if document["format"] != IR_FORMAT or document["format_version"] not in _ACCEPTED_VERSIONS:
        raise ValueError(
            f"runtime requires n8n-typed-ir format_version in {sorted(_ACCEPTED_VERSIONS)}, "
            f"got {document.get('format_version')!r}"
        )
    if verify_digest:
        verify_typed_ir_digest(document)

    workflow = _expect_dict(document["workflow"], "typed IR workflow")
    # P1-1（v5）：settings 可选——真 v1 文档无此字段，不得被 required 拒绝；
    # :155 的 `if "settings" in workflow` 分支随之复活（v2 空 settings 同样适用）。
    _validate_fields(workflow, required=_WORKFLOW_FIELDS - {"settings"},
                     allowed=_WORKFLOW_FIELDS, path="typed IR workflow")
    for key in ("id", "version", "exit_key"):
        _expect_string(workflow[key], f"typed IR workflow.{key}")
    entry_keys = _expect_list(workflow["entry_keys"], "typed IR workflow.entry_keys")
    # 允许空：n8n 无 trigger 工作流 = 手动执行模式（编辑器合法状态）
    for entry in entry_keys:
        _expect_string(entry, "typed IR workflow.entry_keys[]")
    if workflow["exit_key"] != EXIT_NODE_KEY:
        raise ValueError(f"typed IR exit_key must be {EXIT_NODE_KEY}")

    nodes = _expect_list(document["nodes"], "typed IR nodes")
    if not nodes:
        raise ValueError("typed IR nodes must not be empty")
    node_map: dict[str, dict[str, Any]] = {}
    for index, raw_node in enumerate(nodes):
        node = _expect_dict(raw_node, f"typed IR nodes[{index}]")
        _validate_node(node, index)
        key = node["key"]
        if key in node_map:
            raise ValueError(f"typed IR contains duplicate node key {key}")
        node_map[key] = node

    exit_node = node_map.get(EXIT_NODE_KEY)
    if exit_node is None or exit_node["config"].get("n8n_type") != "synthetic.exit":
        raise ValueError(f"typed IR node {EXIT_NODE_KEY} must be synthetic.exit")
    for key in entry_keys:
        if key not in node_map:
            raise ValueError(f"typed IR entry node {key} not found in nodes")
    if "settings" in workflow:
        settings = workflow["settings"]
        if not isinstance(settings, dict):
            raise ValueError("typed IR workflow.settings must be an object")
        for skey in settings:
            _expect_string(skey, f"typed IR workflow.settings key")

    hierarchy = _expect_dict(document["hierarchy"], "typed IR hierarchy")
    for key, parent in hierarchy.items():
        _expect_string(key, "typed IR hierarchy key")
        _expect_string(parent, "typed IR hierarchy value")

    _validate_connections(document["connections"], node_map)
    _validate_execution_order(
        document["execution_order"], node_map, document["connections"]
    )
    _validate_manifest(document["manifest"])


def _validate_node(node: dict[str, Any], index: int) -> None:
    """校验单个 IR 节点。"""
    path = f"typed IR nodes[{index}]"
    _validate_fields(node, required={"key", "type", "name", "config"}, allowed=_NODE_FIELDS,
                     path=path)
    key = _expect_string(node["key"], f"{path}.key")
    _expect_string(node["type"], f"{path}.type")
    _expect_string(node["name"], f"{path}.name")
    if node.get("parent_key") is not None:
        _expect_string(node["parent_key"], f"{path}.parent_key")
    for name, info in node.get("input_types", {}).items():
        _validate_type(_expect_dict(info, f"{path}.input_types.{name}"), f"{path}.input_types.{name}")
    for name, info in node.get("output_types", {}).items():
        _validate_type(_expect_dict(info, f"{path}.output_types.{name}"), f"{path}.output_types.{name}")
    for field_info in node.get("input_sources", []):
        _validate_field_info(_expect_dict(field_info, f"{path}.input_sources"), f"{path}.input_sources")
    for field_info in node.get("output_sources", []):
        _validate_field_info(_expect_dict(field_info, f"{path}.output_sources"), f"{path}.output_sources")
    _validate_error_policy(node.get("error_policy", {}), f"{path}.error_policy")
    _validate_dependencies(node.get("dependencies", {}), f"{path}.dependencies")
    _validate_config(node["config"], f"{path}.config")


def _validate_config(config: dict[str, Any], path: str) -> None:
    _validate_fields(config, required={"kind", "n8n_type"}, allowed=_CONFIG_FIELDS, path=path)
    _expect_string(config["kind"], f"{path}.kind")
    _expect_string(config["n8n_type"], f"{path}.n8n_type")
    if "js" in config:
        js = _expect_dict(config["js"], f"{path}.js")
        _validate_fields(js, required={"contract", "payload", "errors", "warnings"}, allowed={"contract", "payload", "errors", "warnings"}, path=f"{path}.js")
        contract = _expect_dict(js["contract"], f"{path}.js.contract")
        _expect_string(contract.get("effect", ""), f"{path}.js.contract.effect")
        _expect_string(contract.get("runtime", ""), f"{path}.js.contract.runtime")
    if "js_ast" in config and config["js_ast"] is not None:
        if not isinstance(config["js_ast"], dict):
            raise ValueError(f"{path}.js_ast must be an object")


def _validate_type(info: dict[str, Any], path: str) -> None:
    _validate_fields(info, required={"type"}, allowed=_TYPE_FIELDS, path=path)
    t = _expect_string(info["type"], f"{path}.type")
    if t not in _DATA_TYPES:
        raise ValueError(f"{path}.type must be one of {sorted(_DATA_TYPES)}")
    if "elem_type_info" in info:
        _validate_type(_expect_dict(info["elem_type_info"], f"{path}.elem_type_info"), f"{path}.elem_type_info")
    if "properties" in info:
        props = _expect_dict(info["properties"], f"{path}.properties")
        for name, sub in props.items():
            _validate_type(_expect_dict(sub, f"{path}.properties.{name}"), f"{path}.properties.{name}")


def _validate_field_info(field_info: dict[str, Any], path: str) -> None:
    _validate_fields(field_info, required={"path", "source"}, allowed=_FIELD_INFO_FIELDS, path=path)
    _expect_list(field_info["path"], f"{path}.path")
    source = _expect_dict(field_info["source"], f"{path}.source")
    _validate_fields(source, required=set(), allowed=_SOURCE_FIELDS, path=f"{path}.source")
    if "ref" in source:
        ref = _expect_dict(source["ref"], f"{path}.source.ref")
        _validate_fields(ref, required={"from_node_key"}, allowed=_REF_FIELDS, path=f"{path}.source.ref")
        _expect_string(ref["from_node_key"], f"{path}.source.ref.from_node_key")
        if "from_path" in ref:
            _expect_list(ref["from_path"], f"{path}.source.ref.from_path")
        if "variable_type" in ref:
            vt = _expect_string(ref["variable_type"], f"{path}.source.ref.variable_type")
            if vt not in _GLOBAL_VARIABLE_TYPES:
                raise ValueError(f"{path}.source.ref.variable_type must be one of {sorted(_GLOBAL_VARIABLE_TYPES)}")


def _validate_error_policy(policy: Any, path: str) -> None:
    policy = _expect_dict(policy, path)
    _validate_fields(policy, required=set(), allowed=_ERROR_POLICY_FIELDS, path=path)
    on_error = policy.get("on_error")
    if on_error is not None:
        _expect_string(on_error, f"{path}.on_error")
        if on_error not in _ERROR_POLICY_VALUES:
            raise ValueError(f"{path}.on_error must be one of {sorted(_ERROR_POLICY_VALUES)}")


def _validate_dependencies(deps: Any, path: str) -> None:
    deps = _expect_dict(deps, path)
    _validate_fields(deps, required=set(), allowed=_DEPENDENCY_FIELDS, path=path)
    for bucket in ("direct", "indirect", "parent"):
        mapping_by_node = _expect_dict(deps.get(bucket, {}), f"{path}.{bucket}")
        for source_key, mappings in mapping_by_node.items():
            for mapping in _expect_list(mappings, f"{path}.{bucket}.{source_key}"):
                m = _expect_dict(mapping, f"{path}.{bucket}.{source_key}")
                _validate_fields(m, required={"from_path", "to_path"}, allowed=_MAPPING_FIELDS, path=f"{path}.{bucket}.{source_key}")
                _expect_list(m["from_path"], f"{path}.{bucket}.{source_key}.from_path")
                _expect_list(m["to_path"], f"{path}.{bucket}.{source_key}.to_path")
    for static in deps.get("static_values", []):
        s = _expect_dict(static, f"{path}.static_values")
        _validate_fields(s, required={"path", "value"}, allowed=_STATIC_VALUE_FIELDS, path=f"{path}.static_values")
        _expect_list(s["path"], f"{path}.static_values.path")
    for variable in deps.get("variables", []):
        v = _expect_dict(variable, f"{path}.variables")
        _validate_fields(v, required={"variable_type", "from_path", "to_path"}, allowed=_VARIABLE_FIELDS, path=f"{path}.variables")
        vt = _expect_string(v["variable_type"], f"{path}.variables.variable_type")
        if vt not in _GLOBAL_VARIABLE_TYPES:
            raise ValueError(f"{path}.variables.variable_type must be one of {sorted(_GLOBAL_VARIABLE_TYPES)}")
        _expect_list(v["from_path"], f"{path}.variables.from_path")
        _expect_list(v["to_path"], f"{path}.variables.to_path")


def _validate_connections(connections: Any, node_map: dict[str, dict[str, Any]]) -> None:
    connections = _expect_list(connections, "typed IR connections")
    for index, raw in enumerate(connections):
        conn = _expect_dict(raw, f"typed IR connections[{index}]")
        _validate_fields(conn, required=_CONNECTION_FIELDS, allowed=_CONNECTION_ALLOWED_FIELDS,
                         path=f"typed IR connections[{index}]")
        from_node = _expect_string(conn["from_node"], f"typed IR connections[{index}].from_node")
        to_node = _expect_string(conn["to_node"], f"typed IR connections[{index}].to_node")
        from_port = _expect_string(conn["from_port"], f"typed IR connections[{index}].from_port")
        to_port = _expect_string(conn["to_port"], f"typed IR connections[{index}].to_port")
        conn_type = _expect_string(
            conn.get("conn_type", "main"),
            f"typed IR connections[{index}].conn_type",
        )
        # 连接类型白名单：main 数据流 + ai_* 子连接（n8n ConnectionTypes 全集
        # 除 main 外均为 ai_* 前缀）。未知类型显式拒绝，防未来类型静默失真。
        if conn_type != "main" and not conn_type.startswith("ai_"):
            raise ValueError(
                f"typed IR connection has unknown conn_type {conn_type!r}"
            )
        if "to_index" in conn:
            if not isinstance(conn["to_index"], int) or isinstance(conn["to_index"], bool):
                raise ValueError(
                    f"typed IR connections[{index}].to_index must be an integer"
                )
        if from_node not in node_map:
            raise ValueError(f"typed IR connection references unknown node {from_node}")
        if to_node not in node_map:
            raise ValueError(f"typed IR connection references unknown node {to_node}")
        if not _BRANCH_PORT.match(from_port):
            raise ValueError(f"typed IR connection has invalid from_port {from_port!r}")
        # to_port 校验（P1-1c 放宽）：main 边必须 "main"；ai 边（n8n 无 to_port
        # 概念）允许 "main"（编译器编码）或与 conn_type 相同的显式值。
        if to_port != "main":
            if conn_type == "main" or to_port != conn_type:
                raise ValueError(
                    f"typed IR connection to_port must be 'main' "
                    f"(ai 边可为其 conn_type), got {to_port!r}"
                )


def _main_topology_nodes(connections: Any, node_map: dict[str, dict[str, Any]]) -> set[str]:
    """main 拓扑参与节点 = 全部节点 - AI 子节点（仅经 ai_* 连接、无 main 边）。

    与 compiler.workflow._ai_only_subnodes 同规则：execution_order 只对
    main 数据流做全排列断言，AI 子节点（运行时由 agent 拉取）不在其内。
    """
    main_participants: set[str] = set()
    ai_sources: set[str] = set()
    for raw in connections:
        conn = _expect_dict(raw, "typed IR connections[]")
        if conn.get("conn_type", "main") == "main":
            main_participants.add(conn["from_node"])
            main_participants.add(conn["to_node"])
        else:
            ai_sources.add(conn["from_node"])
    return set(node_map) - (ai_sources - main_participants)


def _validate_execution_order(order: Any, node_map: dict[str, dict[str, Any]],
                              connections: Any) -> None:
    order = _expect_dict(order, "typed IR execution_order")
    expected = _main_topology_nodes(connections, node_map)
    for scope, keys in order.items():
        _expect_string(scope, "typed IR execution_order scope")
        keys = _expect_list(keys, f"typed IR execution_order.{scope}")
        if set(keys) != expected:
            raise ValueError(
                f"typed IR execution_order.{scope} must be a permutation of "
                f"the {len(expected)} main-topology node keys (AI 子节点除外), "
                f"got {len(keys)} keys"
            )
        if len(keys) != len(set(keys)):
            raise ValueError(f"typed IR execution_order.{scope} contains duplicate keys")


def _validate_manifest(manifest: Any) -> None:
    manifest = _expect_dict(manifest, "typed IR manifest")
    _validate_fields(manifest, required={"bind_status", "requires"},
                     allowed={"bind_status", "requires", "ai_connections_dropped"},
                     path="typed IR manifest")
    if "ai_connections_dropped" in manifest:
        dropped = manifest["ai_connections_dropped"]
        if not isinstance(dropped, int) or isinstance(dropped, bool):
            raise ValueError("typed IR manifest.ai_connections_dropped must be an int")
        if dropped < 0:
            raise ValueError("typed IR manifest.ai_connections_dropped must be >= 0")
    bind_status = _expect_dict(manifest["bind_status"], "typed IR manifest.bind_status")
    requires = _expect_dict(manifest["requires"], "typed IR manifest.requires")
    for name, refs in requires.items():
        for ref in _expect_list(refs, f"typed IR manifest.requires.{name}"):
            ref = _expect_dict(ref, f"typed IR manifest.requires.{name}")
            _expect_string(ref.get("id", ""), f"typed IR manifest.requires.{name}.id")


def _validate_fields(value: dict[str, Any], *, required: set[str], allowed: set[str], path: str) -> None:
    """白名单字段校验（允许全集 = required ∪ allowed 外的可选字段）。"""
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    for key in required:
        if key not in value:
            raise ValueError(f"{path} missing required field {key!r}")
    for key in value:
        if key not in allowed:
            raise ValueError(f"{path} has unknown field {key!r}")


def _expect_dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _expect_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list")
    return value


def _expect_string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    return value
