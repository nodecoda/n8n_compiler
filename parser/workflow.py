"""n8n Workflow JSON -> WorkflowAST（对齐 coze_compiler.parser.canvas）。

流程：
  1. 反序列化节点（adapt_node，key = 节点名）
  2. 展开 IConnections -> Connection 列表（main[i][j]；非 main 的 AI 子连接 v1 跳过）
  3. 合成 Exit 节点：所有末端节点（无出边、非 sink）接入输出汇
  4. 表达式绑定：参数 "={{ }}" -> input_sources（INPUT 绑定入边上游；$node/$env 直绑）
  5. CodeNode 边界绑定：js_contract.deps（items/$node）-> input_sources
  6. 构建符号表（WORKFLOW scope：节点输出 + 全局 + 输入）
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from ast_nodes.configs import N8NErrorPolicy
from ast_nodes.connection import Connection
from ast_nodes.node_decls import CodeNode, ExitNode, IfNode, NodeDecl
from ast_nodes.node_type import spec_for
from ast_nodes.node_type import EXIT_NODE_KEY, NodeKind, ShapeKind
from ast_nodes.nodes import WorkflowAST
from parser.node_adaptors import _LANGCHAIN_CODE_TYPE, _code_source, adapt_node
from parser.expression import ExprKind, ParsedRef, parse_value
from scope.scope import Scope, ScopeLevel
from scope.symbol import Symbol, SymbolKind
from scope.symbol_table import SymbolTable
from type_system.typeinfo import TypeInfo
from values.reference import FieldInfo, Reference, Source

_MAIN = "main"


def _iter_parameters(parameters: dict[str, Any], prefix: tuple[str, ...] = ()):
    """递归遍历参数，产出 (json_path, value)。数组索引用 "*" 占位。"""
    for key, value in parameters.items():
        path = prefix + (str(key),)
        if isinstance(value, dict):
            yield from _iter_parameters(value, path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, (dict, list)):
                    yield from _iter_parameters(item, path + ("*",))
                else:
                    yield path + (str(index),), item
        else:
            yield path, value


def _port_name_for(node: NodeDecl, index: int) -> str:
    """main 输出端口索引 -> 端口名。

    n8n 语义：单输出节点端口名恒 "main"；多输出节点为 "main_{i}"
    （IF true/false、Switch 各 route、SplitInBatches loop/done）。注册表
    声明了 output_ports>1 的节点按 main_{i} 命名；单端口声明但实际
    connections 出现更高索引（Switch 多 route 超过注册表下限）时宽容
    放行为 main_{i}，不报错。
    """
    spec = spec_for(node.n8n_type)
    if spec.output_ports > 1 or index != 0:
        return f"main_{index}"
    return _MAIN


def _declared_port_count(node: NodeDecl) -> Optional[int]:
    """多输出节点声明的输出端口数（从参数推导）。

    n8n Switch v3：parameters.rules.values（list，每项一路由）；老版本
    parameters.rules 直接是 list。推导失败返回 None（调用方回退注册表）。
    """
    if node.n8n_type != "n8n-nodes-base.switch":
        return None
    # n8n Switch V3: mode='expression' 时输出端口数 = numberOutputs
    # （SwitchV3.node.ts:24-29），与 rules 无关；非 int/缺失回退 None 走注册表。
    if node.parameters.get("mode") == "expression":
        n = node.parameters.get("numberOutputs")
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return None
        # JSON 数值不分 int/float：6 与 6.0 都按整值端口数处理
        return int(n) if isinstance(n, int) or n.is_integer() else None
    rules = node.parameters.get("rules")
    count: Optional[int] = None
    if isinstance(rules, dict) and isinstance(rules.get("values"), list):
        count = len(rules["values"])
    elif isinstance(rules, list):
        count = len(rules)
    else:
        options = node.parameters.get("options")
        if isinstance(options, dict) and isinstance(options.get("rules"), list):
            count = len(options["rules"])
    if count is None:
        return None
    # n8n Switch V3: fallbackOutput 'extra' 在 rules.length 索引处加一输出端口，
    # 未匹配项路由到该端口（SwitchV3.node.ts:292,469-475）——终端场景漏收口
    # 会导致未匹配数据静默丢失（架构审核 P1-1）。
    options = node.parameters.get("options")
    if isinstance(options, dict) and options.get("fallbackOutput") == "extra":
        count += 1
    return count


def _output_port_names(node: NodeDecl) -> list[str]:
    """节点输出端口名（Exit 收口用）。

    单输出节点恒 main；多输出节点（Switch 等路由数运行时才确定）按参数声明
    的路由数生成 main_{0..n-1}，参数不可推导时回退注册表声明数。终端节点无
    出边，无法从 connections 得知实际端口，必须依赖参数推导（注册表 4 是
    下限，路由 >4 时按注册表会漏收口）。
    """
    spec = spec_for(node.n8n_type)
    if spec.output_ports > 1:
        count = _declared_port_count(node) or spec.output_ports
        return [f"main_{i}" for i in range(count)]
    return [_MAIN]


def _single_upstream(conns: list[Connection], node_key: str) -> Optional[str]:
    """节点的唯一 main 上游（用于 $json 绑定）。多上游 -> None（保守）。"""
    ups = [c.from_node for c in conns if c.to_node == node_key and c.to_port == _MAIN]
    if len(ups) == 1:
        return ups[0]
    return None


def _active_code_branch(name: str, raw_connections: Optional[dict]) -> Optional[str]:
    """langchain.code 双模式分流（P2-1，v5）：按节点实际输出连接判定 active 变体。

    main 出边 → "execute"（runCodeAllItems，Main 输出）；仅 ai_* 出边 →
    "supplyData"（工厂）；双分支并存时以 main 为准（主图执行 execute()）。
    无出边 → None（调用方回退兼容顺序）。与 n8n 语义对齐：root 节点走 execute、
    sub-node 走 supplyData。
    """
    if not raw_connections:
        return None
    has_main = has_ai = False
    for conn_type, ports in raw_connections.get(name, {}).items():
        for port in ports or []:
            if port:
                if conn_type == _MAIN:
                    has_main = True
                elif conn_type.startswith("ai_"):
                    has_ai = True
    if has_main:
        return "execute"
    if has_ai:
        return "supplyData"
    return None


def _precompile_code_nodes(raw_nodes: list[dict[str, Any]],
                           raw_connections: Optional[dict] = None) -> dict[str, tuple[Any, Optional[dict]]]:
    """批量预编译全部 Code 节点（一次 acorn 进程）。

    返回 {node_name: (StaticContract, estree_ast|None)}；无 Code 节点或均无源码
    时返回空 dict。空源码节点由 adapt_node 走错误契约路径。
    """
    jobs: list[tuple[str, str, str]] = []  # (name, source, mode)
    for raw in raw_nodes:
        # P1-2（v4）：注册表驱动——凡 kind=CODE 的节点（base.code +
        # langchain.code/toolCode 等）一律进 acorn 批量通道，避免类型白名单漏扩。
        if spec_for(raw.get("type", "")).kind != NodeKind.CODE:
            continue
        params = raw.get("parameters") or {}
        ntype = raw.get("type", "")
        branch = None
        if ntype == _LANGCHAIN_CODE_TYPE:
            # P2-1（v5）：双模式以实际输出连接分流（main → execute / ai → supplyData）
            branch = _active_code_branch(raw.get("name", ""), raw_connections)
        source, mode = _code_source(params, node_type=ntype, branch=branch)
        if source.strip():
            jobs.append((raw.get("name", ""), source, mode))
    if not jobs:
        return {}
    from code import compile_js_batch
    contracts = compile_js_batch(
        [source for _, source, _ in jobs],
        modes=[mode for _, _, mode in jobs],
    )
    return {name: (contract, estree) for (name, _, _), (contract, estree) in zip(jobs, contracts)}


def parse_workflow(data: dict[str, Any]) -> WorkflowAST:
    """n8n Workflow JSON -> WorkflowAST。"""
    raw_nodes = list(data.get("nodes", []) or [])
    js_cache = _precompile_code_nodes(raw_nodes, data.get("connections") or {})
    nodes: dict[str, NodeDecl] = {}
    for raw in raw_nodes:
        node = adapt_node(raw, js_cache=js_cache)
        if node.key in nodes:
            raise ValueError(f"duplicate node name in workflow: {node.key!r}")
        nodes[node.key] = node

    connections: list[Connection] = []
    ai_connections: list[Connection] = []
    for source_name, conn_map in (data.get("connections") or {}).items():
        if source_name not in nodes:
            raise ValueError(f"connection references unknown node: {source_name!r}")
        for conn_type, ports in conn_map.items():
            if conn_type != _MAIN:
                # P1-1c（v4）：非 main 子连接（ai_languageModel/ai_tool/ai_embedding
                # 等）完整携带进 ai_connections，不再丢弃——v2 IR 结构无损。
                # 方向按 n8n 原样记录（子节点 -> 主节点，与 main 相反）；
                # from_port 复用 main 的端口索引编码（"main"/"main_N"）承载
                # 该 ai 连接在 connections[src][conn_type] 下的端口下标。
                for port_index, edges in enumerate(ports or []):
                    for edge in edges or []:
                        target = edge.get("node", "")
                        if target not in nodes:
                            raise ValueError(
                                f"connection {source_name} -> unknown node: {target!r}"
                            )
                        ai_connections.append(Connection(
                            from_node=source_name,
                            from_port=f"main_{port_index}" if port_index else _MAIN,
                            to_node=target,
                            to_index=int(edge.get("index", 0)),
                            conn_type=conn_type,
                        ))
                continue  # AI 子连接不参与 main 数据流拓扑（agent 运行时拉取）
            for port_index, edges in enumerate(ports or []):
                port_name = _port_name_for(nodes[source_name], port_index)
                for edge in edges or []:
                    target = edge.get("node", "")
                    if target not in nodes:
                        raise ValueError(
                            f"connection {source_name} -> unknown node: {target!r}"
                        )
                    connections.append(Connection(
                        from_node=source_name,
                        from_port=port_name,
                        to_node=target,
                        to_index=int(edge.get("index", 0)),
                    ))

    # 合成 Exit：main 数据流末端（有 main 入边、无 main 出边、非 sink）。
    # 装饰节点（stickyNote 等）与 AI 子节点（经 ai_* 连接、无 main 边）不接 exit。
    _DECORATION_TYPES = {"n8n-nodes-base.stickyNote", "n8n-nodes-base.n8nNote"}
    nodes = {k: n for k, n in nodes.items() if n.n8n_type not in _DECORATION_TYPES}
    all_edges = []
    for source_name, conn_map in (data.get("connections") or {}).items():
        for conn_type, ports in conn_map.items():
            for port_index, edges in enumerate(ports or []):
                for edge in edges or []:
                    all_edges.append((conn_type, source_name, edge.get("node", "")))
    main_outgoing = {c.from_node for c in connections}
    main_incoming = {c.to_node for c in connections}
    # ai_* 子连接（方向：子节点 -> 主节点）：两端均不参与 main 拓扑。
    # 源端子节点（Embeddings/Chat Model）无 main 边是合法的；目标端主节点在
    # n8n 中必有 main 边，跳过孤立检查不引入漏检风险。
    ai_referenced = {n for t, s, e in all_edges if t != _MAIN for n in (s, e)}
    connected_ports: dict[str, set[str]] = {}
    for c in connections:
        connected_ports.setdefault(c.from_node, set()).add(c.from_port)
    sink_kinds = {NodeKind.RESPOND}
    terminals = [
        key for key in nodes
        if key in main_incoming and key not in main_outgoing
        and nodes[key].node_type not in sink_kinds and key not in ai_referenced
    ]
    if EXIT_NODE_KEY in nodes:
        # P2-11：用户节点占用了编译器保留名（n8n 不保留字，用户可叫 __exit__）。
        # 静默覆盖会造成假自环报错或节点丢失，必须显式失败并给出可操作信息。
        raise ValueError(
            f"node name {EXIT_NODE_KEY!r} is reserved by the compiler for "
            "the synthetic exit node; rename the node"
        )
    exit_node = ExitNode(key=EXIT_NODE_KEY, name="__exit__")
    nodes[EXIT_NODE_KEY] = exit_node
    for key in terminals:
        for port in _output_port_names(nodes[key]):
            # 终端节点无 main 出边，connected_ports 恒空；守卫保留防御
            if port not in connected_ports.get(key, set()):
                connections.append(Connection(from_node=key, from_port=port, to_node=EXIT_NODE_KEY))

    # 表达式绑定 + CodeNode 边界绑定
    input_sources_by_node: dict[str, list[FieldInfo]] = {key: [] for key in nodes}
    for key, node in nodes.items():
        if isinstance(node, CodeNode):
            _bind_code_node(node, connections, input_sources_by_node)
        for path, value in _iter_parameters(node.parameters):
            if isinstance(node, CodeNode) and path[-1] in ("jsCode", "code"):
                # Code 源码是字面量：n8n 运行时不对 jsCode 做表达式插值
                # （task-runner 原样包进 async function），{{ }} 不构成依赖；
                # Code 依赖已由 acorn 静态通道 _bind_code_node 独立处理
                # （架构审核 P1-2：曾把字面量误绑成引用拒绝合法工作流）。
                continue
            ref, _dynamic = parse_value(value)
            if ref is None:
                continue
            _bind_expr_ref(node, ref, path, connections, input_sources_by_node)

    for key, sources in input_sources_by_node.items():
        nodes[key].input_sources = sources

    # 符号表：WORKFLOW scope（节点输出 + 全局）+ 每节点 scope
    table = SymbolTable()
    workflow_scope = Scope(name="__root__", level=ScopeLevel.WORKFLOW)
    table.register_scope(workflow_scope)
    for key, node in nodes.items():
        workflow_scope.define(Symbol(name=key, type=TypeInfo.any(), kind=SymbolKind.OUTPUT, source_node=key), replace=True)
        node_scope = Scope(name=key, level=ScopeLevel.NODE, parent=workflow_scope)
        table.register_scope(node_scope)
        node.scope = node_scope

    # P3-4（v5）：settings 类型守卫——非对象源若透传，编译器会产出自身
    # loader 拒绝的 IR（typed_ir 装载才报错，错误来得太晚）；parse 期显式拒绝。
    _raw_settings = data.get("settings")
    if _raw_settings is None:
        settings: dict[str, Any] = {}
    elif isinstance(_raw_settings, dict):
        settings = _raw_settings
    else:
        raise ValueError(
            "workflow.settings must be an object, "
            f"got {type(_raw_settings).__name__}"
        )

    ast = WorkflowAST(
        nodes=nodes,
        connections=connections,
        ai_connections=ai_connections,
        # P1-1c：非 main 连接全部携带，本字段为「携带边数」观测（类型 -> 边数）
        non_main_connections=dict(Counter(c.conn_type for c in ai_connections)),
        ai_referenced=ai_referenced,
        symbol_table=table,
        settings=settings,
        pin_data=data.get("pinData") or {},
        meta=data.get("meta") or {},
    )
    return ast


def _bind_expr_ref(
    node: NodeDecl,
    ref: ParsedRef,
    path: tuple[str, ...],
    connections: list[Connection],
    out: dict[str, list[FieldInfo]],
) -> None:
    """表达式引用 -> input_sources 条目。"""
    if ref.kind == ExprKind.NODE:
        out[node.key].append(FieldInfo(
            path=list(path),
            source=Source(ref=Reference(from_node_key=ref.node, from_path=list(ref.path))),
        ))
    elif ref.kind == ExprKind.INPUT:
        upstream = _single_upstream(connections, node.key)
        if upstream is not None:
            out[node.key].append(FieldInfo(
                path=list(path),
                source=Source(ref=Reference(from_node_key=upstream, from_path=list(ref.path))),
            ))
        # 无唯一上游（trigger 或多上游）：$json 引用运行时才可知，不绑定
    elif ref.kind == ExprKind.GLOBAL:
        out[node.key].append(FieldInfo(
            path=list(path),
            source=Source(ref=Reference(from_node_key="", from_path=list(ref.path), variable_type=ref.var_type)),
        ))


def _bind_code_node(
    node: CodeNode,
    connections: list[Connection],
    out: dict[str, list[FieldInfo]],
) -> None:
    """CodeNode 作用域边界绑定：js_contract.deps -> input_sources。

    - items/item/$json/$input 依赖 -> 入边唯一上游（Reference）
    - $node["X"].y 依赖 -> 显式节点引用（Reference(node=X, path=y)）
    JS 内部词法作用域不外泄（见 ast_nodes.node_decls.CodeNode 文档）。
    """
    if node.js_contract is None:
        return
    upstream = _single_upstream(connections, node.key)
    for dep in node.js_contract.contract.deps:
        if dep.base in ("items", "item", "$json", "$input"):
            if upstream is not None:
                out[node.key].append(FieldInfo(
                    path=["items", *dep.path],
                    source=Source(ref=Reference(from_node_key=upstream, from_path=list(dep.path))),
                ))
        elif dep.base == "$node" and dep.path:
            ref_path = list(dep.path[1:])
            out[node.key].append(FieldInfo(
                path=["items", *dep.path],
                source=Source(ref=Reference(from_node_key=dep.path[0], from_path=ref_path)),
            ))
