"""IR -> n8n JSON 反编译 round-trip：编译无损性验证。

n8n JSON -> parse -> compile -> IR -> decompile -> n8n JSON，断言语义等价
（节点集合同构、连接集合同构、表达式参数原样保留）。合成 __exit__ 被剔除。
"""
import json
import unittest

from compiler.workflow import compile_ast
from parser.workflow import parse_workflow
from runtime.decompile import (
    _port_index,  # 白盒：畸形分支入口被 typed_ir 挡，仅单测可达
    decompile_ir_json,
    decompile_to_workflow,
)
from tests.helpers import (
    chain_workflow,
    code_node,
    manual_trigger_node,
    mini_webhook_workflow,
    rag_fixture,
    set_node,
    switch_workflow,
)


def _compile(wf: dict) -> dict:
    return compile_ast(parse_workflow(wf), workflow_id="t", version="1").to_dict()


def _roundtrip(wf: dict, name: str | None = "orig") -> dict:
    if name is None:
        return decompile_to_workflow(_compile(wf))  # 用 decompile 默认名
    return decompile_to_workflow(_compile(wf), name=name)


def _canonical_nodes(n8n_wf: dict) -> dict:
    """name -> (type, typeVersion, position, parameters) 规范化。

    JSON 数值不分 int/float，2 与 2.0、[0,0] 与 [0.0,0.0] 语义等价；
    统一归一到 int/float 后再比较，避免 Python 类型差异误报。
    """
    out = {}
    for node in n8n_wf["nodes"]:
        out[node["name"]] = (
            node["type"],
            int(node.get("typeVersion", 1)),
            [float(x) for x in (node.get("position") or [0, 0])],
            node.get("parameters") or {},
        )
    return out


def _canonical_edges(n8n_wf: dict) -> set[tuple]:
    """(from, port_index, to, to_index) 边集合（n8n 形状 -> 元组）。"""
    edges = set()
    for src, conn_types in (n8n_wf.get("connections") or {}).items():
        for conn_type, ports in conn_types.items():
            if conn_type != "main":
                continue
            for port_index, edge_list in enumerate(ports):
                for edge in edge_list or []:
                    edges.add((src, port_index, edge["node"], edge.get("index", 0)))
    return edges


def _canonical_edges_all(n8n_wf: dict) -> list[tuple]:
    """全部连接（含 ai_*）多集五元组：(src, conn_type, port_index, to, to_index)。

    P2-3（v5）：保留重复边（列表非集合）——多集守恒才能测出 fan-out 丢边；
    类型级计数（Counter）无法区分「1 条边」与「2 条同型边」。
    """
    edges: list[tuple] = []
    for src, conn_types in (n8n_wf.get("connections") or {}).items():
        for conn_type, ports in conn_types.items():
            for port_index, edge_list in enumerate(ports):
                for edge in edge_list or []:
                    edges.append((src, conn_type, port_index, edge["node"],
                                  edge.get("index", 0)))
    return sorted(edges)


def _assert_equivalent(test: unittest.TestCase, original: dict, back: dict):
    """语义等价断言：节点/连接集合一致；合成 exit 不在产物中。"""
    orig_nodes = _canonical_nodes(original)
    back_nodes = _canonical_nodes(back)
    test.assertEqual(set(back_nodes), set(orig_nodes))
    for name in orig_nodes:
        test.assertEqual(back_nodes[name], orig_nodes[name], f"node {name}")
    test.assertEqual(_canonical_edges(back), _canonical_edges(original))
    # 无合成节点
    test.assertTrue(all(n["type"] not in ("synthetic.entry", "synthetic.exit")
                        for n in back["nodes"]))
    test.assertTrue(all(edge[2] != "__exit__" for edge in _canonical_edges(back)))


class TestRoundTrip(unittest.TestCase):
    def test_mini_webhook_round_trip(self):
        wf = mini_webhook_workflow()
        wf["name"] = "mini"
        back = _roundtrip(wf, name="mini")
        self.assertEqual(back["name"], "mini")
        _assert_equivalent(self, wf, back)

    def test_switch_multi_output_round_trip(self):
        wf = switch_workflow(3)
        _assert_equivalent(self, wf, _roundtrip(wf))

    def test_code_node_js_param_preserved(self):
        # Code 节点 jsCode（含表达式串）必须原样往返
        wf = chain_workflow(
            [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                code_node("T", "return { x: $json.a };"),
            ],
            [("W", "T")],
        )
        back = _roundtrip(wf)
        t = next(n for n in back["nodes"] if n["name"] == "T")
        self.assertEqual(t["parameters"]["jsCode"], "return { x: $json.a };")
        _assert_equivalent(self, wf, back)

    def test_expression_parameter_preserved(self):
        wf = chain_workflow(
            [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "S", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1],
                 "parameters": {"assignments": {"values": [
                     {"name": "x", "value": "={{ $json.a }}", "type": "string"}]}}},
            ],
            [("W", "S")],
        )
        _assert_equivalent(self, wf, _roundtrip(wf))

    def test_merge_multi_input_to_index_round_trip(self):
        # Merge 双输入：to_index 0/1 必须还原为 n8n 边 index
        wf = {
            "name": "merge",
            "nodes": [
                {"name": "A", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 0], "parameters": {}},
                {"name": "B", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1], "parameters": {}},
                {"name": "M", "type": "n8n-nodes-base.merge", "typeVersion": 3,
                 "position": [0, 2], "parameters": {}},
            ],
            "connections": {
                "A": {"main": [[{"node": "M", "index": 0}]]},
                "B": {"main": [[{"node": "M", "index": 1}]]},
            },
        }
        back = _roundtrip(wf)
        edges = _canonical_edges(back)
        self.assertIn(("A", 0, "M", 0), edges)
        self.assertIn(("B", 0, "M", 1), edges)
        _assert_equivalent(self, wf, back)


    def test_switch_expression_mode_ports_capped(self):
        # P1-N1 回归（全链路）：终端 Switch mode='expression' + numberOutputs=6，
        # IR 的 exit 收口必须覆盖 main_0..main_5（round-trip 语义无损）。
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3,
                 "position": [0, 1], "parameters": {
                     "mode": "expression", "numberOutputs": 6}},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ir = _compile(wf)
        exit_ports = sorted(c["from_port"] for c in ir["connections"]
                            if c["from_node"] == "Switch")
        self.assertEqual(exit_ports, [f"main_{i}" for i in range(6)])

    def test_settings_round_trip_restored(self):
        # P3-8（v4）：IR v2 携带源 settings 原值；v2 源工作流不再被强制降级 v1
        wf = chain_workflow(
            [manual_trigger_node("Trigger"), set_node("Set")],
            [("Trigger", "Set")],
        )
        wf["settings"] = {"executionOrder": "v2", "timezone": "UTC"}
        back = _roundtrip(wf)
        self.assertEqual(back["settings"], {"executionOrder": "v2", "timezone": "UTC"})

    def test_settings_default_v1_when_absent(self):
        # 源无 settings -> 回退编辑器默认 v1（REST schema 强制字段存在）
        wf = chain_workflow(
            [manual_trigger_node("Trigger"), set_node("Set")],
            [("Trigger", "Set")],
        )
        back = _roundtrip(wf)
        self.assertEqual(back["settings"], {"executionOrder": "v1"})

    def test_node_aux_fields_dropped_documented(self):
        # P3-1（v4）：节点级辅助字段（webhookId/disabled/notes/executeOnce/
        # alwaysOutputData）round-trip 不还原——文档化丢失边界，锁定现状防
        # 未来静默改变（IR 未携带即不还原，属编译语义往返的已知边界）。
        wf = chain_workflow(
            [manual_trigger_node("Trigger"), set_node("Set")],
            [("Trigger", "Set")],
        )
        for node in wf["nodes"]:
            node["webhookId"] = "wh-1"
            node["notes"] = "note"
        wf["nodes"][0]["disabled"] = True
        wf["nodes"][1]["executeOnce"] = True
        wf["nodes"][1]["alwaysOutputData"] = True
        back = _roundtrip(wf)
        for node in back["nodes"]:
            self.assertNotIn("webhookId", node)
            self.assertNotIn("notes", node)
            self.assertNotIn("disabled", node)
            self.assertNotIn("executeOnce", node)
            self.assertNotIn("alwaysOutputData", node)

    def test_workflow_id_restored(self):
        # IR workflow.id -> 产物 id（n8n 导入要求 workflow_entity.id 非空唯一）
        ir = compile_ast(parse_workflow(mini_webhook_workflow()), workflow_id="wf-abc",
                         version="1").to_dict()
        back = decompile_to_workflow(ir)
        self.assertEqual(back["id"], "wf-abc")

    def test_missing_workflow_id_generates_uuid(self):
        # 无 id IR -> UUID（不撞主键；确定性还原留给有 id 的 IR）
        import uuid as uuid_mod
        ir = compile_ast(parse_workflow(mini_webhook_workflow()), workflow_id="",
                         version="1").to_dict()
        back = decompile_to_workflow(ir)
        uuid_mod.UUID(back["id"])  # 非空且是合法 UUID，否则抛 ValueError

    def test_ai_edges_preserved_round_trip(self):
        # P1-1c（v4）/ P2-3（v5）：ai_* 子连接必须 round-trip 守恒——v1 静默
        # 丢弃（3->0），v2 携带还原后不得丢失。P2-3 起断言升级为多集五元组
        # (src, conn_type, port_index, to, to_index)，含重复边计数。
        wf = json.loads(rag_fixture().read_text(encoding="utf-8"))
        wf = wf.get("workflow", wf)

        source = _canonical_edges_all(wf)
        ai_count = sum(1 for e in source if e[1] != "main")
        self.assertGreaterEqual(ai_count, 3)  # fixture 至少 3 条 ai 边
        back = _roundtrip(wf)
        self.assertEqual(_canonical_edges_all(back), source,
                         "ai_* 子连接 round-trip 多集守恒（P1-1c/P2-3）")

    def test_ai_fan_out_multiset_conserved(self):
        # P2-3（v5）：fan-out（同一 ai 端口多边）+ 多输入槽（to_index 区分）
        # 必须多集守恒——类型级计数无法区分同型多边，会漏掉丢边。
        wf = {
            "name": "fanout",
            "nodes": [
                manual_trigger_node("Trigger"),
                {"name": "A1", "type": "@n8n/n8n-nodes-langchain.agent",
                 "typeVersion": 1, "position": [0, 0], "parameters": {}},
                {"name": "A2", "type": "@n8n/n8n-nodes-langchain.agent",
                 "typeVersion": 1, "position": [0, 1], "parameters": {}},
                {"name": "Tool", "type": "@n8n/n8n-nodes-langchain.toolCode",
                 "typeVersion": 1, "position": [0, 2],
                 "parameters": {"language": "javaScript", "jsCode": "return query;"}},
            ],
            "connections": {
                "Trigger": {"main": [[{"node": "A1", "type": "main", "index": 0}],
                                     [{"node": "A2", "type": "main", "index": 0}]]},
                # 同一 ai_tool 端口两条边（fan-out）+ 不同输入槽（to_index 0/1）
                "Tool": {"ai_tool": [[
                    {"node": "A1", "type": "ai_tool", "index": 0},
                    {"node": "A2", "type": "ai_tool", "index": 1},
                ]]},
            },
        }
        source = _canonical_edges_all(wf)
        ai = [e for e in source if e[1] == "ai_tool"]
        self.assertEqual(ai, [("Tool", "ai_tool", 0, "A1", 0),
                              ("Tool", "ai_tool", 0, "A2", 1)])  # 构造形状真实存在
        back = _roundtrip(wf)
        self.assertEqual(_canonical_edges_all(back), source,
                         "fan-out + 多输入槽多集守恒（P2-3）")

    def test_a2_probe_round_trip(self):
        # execute_verify.py 探针同源（Manual Trigger -> Code）：产物结构锁定，
        # 防验证脚本产物漂移（n8n 真实执行已实证 result:42 / status=success）。
        wf = chain_workflow(
            [manual_trigger_node("Trigger"), code_node("Code", "return { result: 42 };")],
            [("Trigger", "Code")],
        )
        wf["name"] = "a2-probe"
        ir = compile_ast(parse_workflow(wf), workflow_id="probe-1", version="1").to_dict()
        back = decompile_to_workflow(ir, name="a2-probe")
        self.assertEqual(back["id"], "probe-1")
        self.assertEqual(back["name"], "a2-probe")
        self.assertEqual(_canonical_edges(back), {("Trigger", 0, "Code", 0)})
        _assert_equivalent(self, wf, back)

    def test_n8n_import_shape(self):
        # A2 实证的 n8n 加载校验最小形状（id 缺失 -> SQLITE_CONSTRAINT 导入失败）：
        # 1) 工作流 id 非空；2) 每节点四要素齐（name/type/typeVersion/position/parameters）；
        # 3) 每条边含 node/type/index。
        back = _roundtrip(switch_workflow(3))
        self.assertTrue(back["id"])
        for node in back["nodes"]:
            for key in ("name", "type", "typeVersion", "position", "parameters"):
                self.assertIn(key, node, f"{node.get('name')} missing {key}")
            self.assertEqual(len(node["position"]), 2)
        for src, conn_types in back["connections"].items():
            for port_index, edge_list in enumerate(conn_types["main"]):
                for edge in edge_list:
                    for key in ("node", "type", "index"):
                        self.assertIn(key, edge, f"edge {src}:{port_index} missing {key}")

    def test_tampered_connections_rejected(self):
        # 篡改连接（改目标节点）同 node name 篡改一样被 digest 拒绝
        ir = _compile(mini_webhook_workflow())
        ir["connections"][0]["to_node"] = "Elsewhere"
        with self.assertRaises(ValueError):
            decompile_to_workflow(ir)

    def test_port_index_bounds(self):
        # main -> 0；main_N -> N；畸形串宽容回 0（typed_ir 校验已在入口拦截）
        self.assertEqual(_port_index("main"), 0)
        self.assertEqual(_port_index("main_0"), 0)
        self.assertEqual(_port_index("main_3"), 3)
        self.assertEqual(_port_index("bogus"), 0)
        self.assertEqual(_port_index("main_x"), 0)


    def test_missing_to_index_defaults_to_zero(self):
        # P2-12 回归：IR to_index 可选（typed_ir 白名单），缺省时 decompile
        # 不得抛 KeyError（破显式失败纪律），按单输入 index=0 处理。
        from typed_ir import compute_typed_ir_digest
        wf = chain_workflow([manual_trigger_node("W"), set_node("S")], [("W", "S")])
        ir = _compile(wf)
        for conn in ir["connections"]:
            conn.pop("to_index", None)
        ir["digest"] = compute_typed_ir_digest(ir)  # 内容变了，重算防篡改 digest
        back = decompile_to_workflow(ir)
        self.assertIn(("W", 0, "S", 0), _canonical_edges(back))

    def test_credentials_restored(self):
        # P2-13 回归：节点 credentials（凭据引用，无敏感值）必须进 IR 并被
        # 反编译还原（曾从未入 IR -> 部署产物丢认证绑定）。
        wf = chain_workflow(
            [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "H", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
                 "position": [0, 1], "parameters": {"url": "https://x", "method": "GET"},
                 "credentials": {"httpHeaderAuth": {"id": "cred-1", "name": "MyAuth"}}},
            ],
            [("W", "H")],
        )
        back = _roundtrip(wf)
        h = next(n for n in back["nodes"] if n["name"] == "H")
        self.assertEqual(h["credentials"], {"httpHeaderAuth": {"id": "cred-1", "name": "MyAuth"}})

    def test_error_policy_restored(self):
        # P2-13 回归：非默认 error_policy 必须回写 n8n 顶层 onError/retry 字段
        wf = chain_workflow(
            [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "S", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1], "parameters": {},
                 "onError": "continueRegularOutput", "retryOnFail": True,
                 "maxTries": 3, "waitBetweenTries": 100},
            ],
            [("W", "S")],
        )
        back = _roundtrip(wf)
        s = next(n for n in back["nodes"] if n["name"] == "S")
        self.assertEqual(s.get("onError"), "continueRegularOutput")
        self.assertEqual(s.get("retryOnFail"), True)
        self.assertEqual(s.get("maxTries"), 3)
        self.assertEqual(s.get("waitBetweenTries"), 100)


class TestDecompileGuards(unittest.TestCase):
    def test_default_name(self):
        back = _roundtrip(mini_webhook_workflow(), name=None)
        self.assertEqual(back["name"], "decompiled")

    def test_tampered_ir_rejected(self):
        ir = _compile(mini_webhook_workflow())
        ir["nodes"][0]["name"] = "Hacked"
        with self.assertRaises(ValueError):  # digest 不匹配
            decompile_to_workflow(ir)

    def test_ir_json_round_trip(self):
        import json
        ir = _compile(mini_webhook_workflow())
        back = decompile_ir_json(json.dumps(ir), name="j")
        self.assertEqual(back["name"], "j")
        self.assertTrue(back["nodes"])

    def test_non_dict_ir_rejected(self):
        with self.assertRaises(ValueError):
            decompile_ir_json('"nope"')

class TestSyntheticEdgeGuards(unittest.TestCase):
    def test_synthetic_entry_out_edge_dropped(self):
        # P2-14 回归：只剔 exit 收口边会留下 from_node 已剔除的悬空连接；
        # synthetic.entry 出边必须一并剔除
        from typed_ir import compute_typed_ir_digest
        wf = chain_workflow([manual_trigger_node("W"), set_node("S")], [("W", "S")])
        ir = _compile(wf)
        ir["nodes"].append({
            "key": "__entry__", "type": "synthetic.entry", "name": "__entry__",
            "parent_key": None,
            "config": {"kind": "entry", "n8n_type": "synthetic.entry",
                       "type_version": 1, "position": [0, 0], "parameters": {},
                       "error_policy": {"on_error": "stopWorkflow"}},
            "input_types": {}, "output_types": {},
            "input_sources": [], "output_sources": [],
        })
        ir["connections"].append({"from_node": "__entry__", "from_port": "main",
                                  "to_node": "W", "to_port": "main", "to_index": 0})
        ir["workflow"]["entry_keys"] = ["__entry__"]
        ir["execution_order"]["__root__"].append("__entry__")  # 排列须覆盖全部 node key
        ir["digest"] = compute_typed_ir_digest(ir)
        back = decompile_to_workflow(ir)
        for src, conn_types in back["connections"].items():
            self.assertNotEqual(src, "__entry__")
            for edge_list in conn_types["main"]:
                for edge in edge_list or []:
                    self.assertNotEqual(edge["node"], "__entry__")
        self.assertTrue(all(n["name"] != "__entry__" for n in back["nodes"]))



if __name__ == "__main__":
    unittest.main()
