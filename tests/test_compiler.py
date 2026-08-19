"""compiler 主干：compile_ast -> n8n-typed-ir v1 严格校验 + digest 往返。"""
import json
import unittest

from ast_nodes.node_type import EXIT_NODE_KEY, ROOT_SCOPE  # noqa: E402
from compiler.workflow import compile_ast  # noqa: E402
from parser.workflow import parse_workflow  # noqa: E402
from tests.helpers import (  # noqa: E402
    chain_workflow,
    code_node,
    langchain_code_execute_node,
    langchain_code_node,
    manual_trigger_node,
    rag_fixture,
    tool_code_node,
    webhook_node,
)
from checker.validator import validate_workflow  # noqa: E402
from ast_nodes.node_decls import CodeNode  # noqa: E402
from typed_ir import (  # noqa: E402
    IR_FORMAT,
    IR_VERSION,
    compute_typed_ir_digest,
    load_typed_ir_json,
    validate_typed_ir,
    verify_typed_ir_digest,
)


def _rag_ast():
    data = json.loads(rag_fixture().read_text(encoding="utf-8"))
    return parse_workflow(data.get("workflow", data))


def _mini_ast():
    wf = chain_workflow(
        [webhook_node("Webhook"), code_node("Transform", "return items.map((it) => ({ text: it.json.text }));", x=1)],
        [("Webhook", "Transform")],
    )
    return parse_workflow(wf)


class TestCompileAst(unittest.TestCase):
    def test_mini_workflow_compiles(self):
        compiled = compile_ast(_mini_ast(), workflow_id="mini", version="1")
        doc = compiled.document
        self.assertEqual(doc["format"], IR_FORMAT)
        self.assertEqual(doc["format_version"], IR_VERSION)
        validate_typed_ir(doc)
        self.assertEqual(doc["workflow"]["entry_keys"], ["Webhook"])
        self.assertEqual(doc["workflow"]["exit_key"], EXIT_NODE_KEY)

    def test_execution_order_is_valid_topology(self):
        compiled = compile_ast(_mini_ast(), workflow_id="mini", version="1")
        order = compiled.document["execution_order"][ROOT_SCOPE]
        self.assertEqual(order[0], "Webhook")
        self.assertIn("Transform", order)
        # 拓扑合法性：每个节点在 connections 中的上游必须排在它前面
        positions = {key: i for i, key in enumerate(order)}
        for conn in compiled.document["connections"]:
            self.assertLess(positions[conn["from_node"]], positions[conn["to_node"]])

    def test_digest_roundtrip(self):
        compiled = compile_ast(_mini_ast(), workflow_id="mini", version="1")
        verify_typed_ir_digest(compiled.document)
        recompiled = compute_typed_ir_digest(compiled.document)
        self.assertEqual(recompiled, compiled.digest)

    def test_code_contract_embedded(self):
        compiled = compile_ast(_mini_ast(), workflow_id="mini", version="1")
        transform = compiled.find_node("Transform")
        js = transform["config"]["js"]
        self.assertEqual(js["errors"], [])
        self.assertEqual(js["contract"]["output"]["kind"], "list")
        self.assertIsNotNone(transform["config"]["js_ast"])


class TestTypedIRValidation(unittest.TestCase):
    def setUp(self):
        if not rag_fixture().exists():
            self.skipTest(f"RAG fixture not present at {rag_fixture()} (set N8N_REPO)")
        self.document = compile_ast(_rag_ast(), workflow_id="rag", version="1").to_dict()
        validate_typed_ir(self.document)

    def test_load_and_verify(self):
        payload = json.dumps(self.document, ensure_ascii=False)
        loaded = load_typed_ir_json(payload)
        self.assertEqual(loaded["format"], IR_FORMAT)

    def test_tampered_digest_rejected(self):
        tampered = dict(self.document)
        tampered["digest"] = "sha256:" + "0" * 64
        with self.assertRaises(ValueError):
            verify_typed_ir_digest(tampered)
        with self.assertRaises(ValueError):
            validate_typed_ir(tampered)

    def test_unknown_field_rejected(self):
        bad = dict(self.document)
        bad["sneaky"] = True
        with self.assertRaises(ValueError):
            validate_typed_ir(bad)

    def test_missing_digest_rejected(self):
        bad = {k: v for k, v in self.document.items() if k != "digest"}
        with self.assertRaises(ValueError):
            validate_typed_ir(bad)

    def test_wrong_format_rejected(self):
        bad = dict(self.document)
        bad["format"] = "coze-typed-ir"
        with self.assertRaises(ValueError):
            validate_typed_ir(bad)

    def test_entry_keys_and_exit(self):
        wf = self.document["workflow"]
        self.assertEqual(wf["exit_key"], EXIT_NODE_KEY)
        self.assertTrue(wf["entry_keys"])
        for key in wf["entry_keys"]:
            self.assertIn(key, {n["key"] for n in self.document["nodes"]})

    def test_unregistered_trigger_entry_key_fallback(self):
        # P2-1（v4）：未知 trigger 类型（落 GENERIC）不再导致 entry_keys 恒空——
        # 注册表新增 3 类 trigger + 零入边回退双保险。这里用已注册的
        # executeWorkflowTrigger 验证注册生效；未注册类型由回退兜底。
        ast = _mini_ast()
        ast2 = parse_workflow({
            "nodes": [
                {"name": "T", "type": "n8n-nodes-base.executeWorkflowTrigger",
                 "typeVersion": 1, "position": [0, 0], "parameters": {}},
                {"name": "S", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1], "parameters": {}},
            ],
            "connections": {"T": {"main": [[{"node": "S"}]]}},
        })
        ir = compile_ast(ast2, workflow_id="x", version="1").to_dict()
        self.assertEqual(ir["workflow"]["entry_keys"], ["T"])
        # 启发式兜底：注册表未覆盖、但类型名含 "trigger" 的节点同样被识别
        ast3 = parse_workflow({
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.someNewTrigger",
                 "typeVersion": 1, "position": [0, 0], "parameters": {}},
            ],
            "connections": {},
        })
        ir3 = compile_ast(ast3, workflow_id="y", version="1").to_dict()
        self.assertEqual(ir3["workflow"]["entry_keys"], ["W"])
        self.assertIsNotNone(ast)

    def test_manifest_ai_carried_counted(self):
        # P1-1c（v4）：parser 统计非 main 连接（携带量），manifest 透出；
        # 字段名 ai_connections_dropped 保留（v1 遗留），语义为「v2 携带的
        # ai 边数」——v2 起不再有结构性丢弃。
        ast = _rag_ast()
        self.assertIsInstance(ast.non_main_connections, dict)
        self.assertIn("ai_languageModel", ast.non_main_connections)
        self.assertGreater(ast.non_main_connections["ai_languageModel"], 0)
        self.assertEqual(self.document["manifest"]["ai_connections_dropped"],
                         sum(ast.non_main_connections.values()))
        # 白名单校验：负数 / 非 int 拒绝
        bad = dict(self.document)
        bad["manifest"] = dict(self.document["manifest"])
        bad["manifest"]["ai_connections_dropped"] = -1
        with self.assertRaises(ValueError):
            validate_typed_ir(bad)

    def test_v2_ai_connections_carry_conn_type(self):
        # P1-1c：IR v2 的 connections 携带 ai_* 子连接（conn_type），结构无损
        self.assertEqual(self.document["format_version"], 2)
        conns = self.document["connections"]
        ai = [c for c in conns if c.get("conn_type") != "main"]
        self.assertGreaterEqual(len(ai), 3)  # fixture 至少 3 条 ai 边
        types = {c["conn_type"] for c in ai}
        self.assertEqual(types, {"ai_embedding", "ai_languageModel"})
        # 方向与 n8n 一致：子节点 -> 主节点
        self.assertTrue(all(
            c["from_node"] in ("Gemini Embeddings - Setup", "Gemini Embeddings - Query",
                               "Gemini Chat Model")
            for c in ai))

    def test_execution_order_excludes_ai_subnodes(self):
        # P1-1c：AI 子节点（仅经 ai_* 连接、无 main 边）不参与 main 拓扑序；
        # typed_ir 按 main 拓扑节点集合做全排列断言
        order = self.document["execution_order"][ROOT_SCOPE]
        self.assertNotIn("Gemini Chat Model", order)
        self.assertNotIn("Gemini Embeddings - Setup", order)
        self.assertNotIn("Gemini Embeddings - Query", order)
        validate_typed_ir(self.document)  # 排列断言通过（含排除后集合）

    def test_ai_conn_to_port_accepts_conn_type(self):
        # P1-1c to_port 放宽：ai 边 to_port 可为 "main"（编译器编码）或 = conn_type
        ok = json.loads(json.dumps(self.document))
        ai = next(c for c in ok["connections"] if c.get("conn_type") != "main")
        ai["to_port"] = ai["conn_type"]
        ok["digest"] = compute_typed_ir_digest(ok)
        validate_typed_ir(ok)  # 不抛

    def test_main_conn_to_port_strict(self):
        # to_port 放宽只对 ai 边：main 边 to_port != "main" 仍显式拒绝
        bad = json.loads(json.dumps(self.document))
        main = next(c for c in bad["connections"] if c.get("conn_type", "main") == "main")
        main["to_port"] = "side"
        bad["digest"] = compute_typed_ir_digest(bad)
        with self.assertRaises(ValueError):
            validate_typed_ir(bad)

    def test_langchain_code_statically_compiled(self):
        # AI 链内联 Code（@n8n/n8n-nodes-langchain.code）：与普通 Code 节点
        # 同走一等公民静态通道——acorn 语法检查 + 契约 + ESTree 进 IR。
        wf = chain_workflow(
            [manual_trigger_node("Trigger"), langchain_code_node("Fake", "return new FakeEmbeddings();")],
            [("Trigger", "Fake")],
        )
        ast = parse_workflow(wf)
        node = ast.nodes["Fake"]
        self.assertIsInstance(node, CodeNode)
        self.assertIsNotNone(node.js_contract)
        self.assertTrue(node.js_contract.ok)
        self.assertIsNotNone(node.js_ast)  # ESTree 保留
        self.assertEqual(node.js_contract.contract.output.kind.value, "object")
        self.assertEqual(validate_workflow(ast), [])  # 语法/契约全过
        ir = compile_ast(ast, workflow_id="lc", version="1").to_dict()
        fake_cfg = next(n["config"] for n in ir["nodes"] if n["key"] == "Fake")
        self.assertIn("js", fake_cfg)
        self.assertIn("js_ast", fake_cfg)
        self.assertEqual(fake_cfg["js"]["errors"], [])  # 无语法错误
        self.assertEqual(fake_cfg["js"]["contract"]["output"]["kind"], "object")
        self.assertEqual(fake_cfg["js"]["contract"]["effect"], "pure")

    def test_langchain_code_bad_syntax_caught(self):
        # AI 链内联 Code 语法错误必须编译期抓出（对齐 test_repo_bad_code_caught 纪律）
        wf = chain_workflow(
            [manual_trigger_node("Trigger"), langchain_code_node("Fake", "return 1aaa;")],
            [("Trigger", "Fake")],
        )
        ast = parse_workflow(wf)
        node = ast.nodes["Fake"]
        self.assertIsInstance(node, CodeNode)
        issues = validate_workflow(ast)
        self.assertTrue(any(i.code == "code_syntax_error" for i in issues),
                        f"expected code_syntax_error, got {[i.code for i in issues]}")

    def test_langchain_code_factory_source_extracted(self):
        # 取源分流：supplyData.code 被正确读出（嵌套两层 parameters.code.supplyData.code）
        wf = chain_workflow(
            [manual_trigger_node("Trigger"), langchain_code_node("Fake", "return new F();")],
            [("Trigger", "Fake")],
        )
        ast = parse_workflow(wf)
        node = ast.nodes["Fake"]
        self.assertEqual(node.js_contract.payload.source, "return new F();")

    def test_langchain_code_execute_variant_compiles(self):
        # P2-1（v5）：langchain.code execute 变体（Main 输出）取源
        # parameters.code.execute.code，mode = runOnceForAllItems（items 全量
        # 数组，对齐 n8n Code.node.ts runCodeAllItems + addItems:true）。
        wf = chain_workflow(
            [manual_trigger_node("Trigger"),
             langchain_code_execute_node("Exec", "return items.map((it) => ({ ok: true }));")],
            [("Trigger", "Exec")],
        )
        ast = parse_workflow(wf)
        node = ast.nodes["Exec"]
        self.assertIsInstance(node, CodeNode)
        self.assertIsNotNone(node.js_contract)
        self.assertTrue(node.js_contract.ok)  # 不再假报 code_syntax_error
        self.assertEqual(node.js_contract.payload.source,
                         "return items.map((it) => ({ ok: true }));")
        self.assertEqual(validate_workflow(ast), [])

    def test_langchain_code_dual_branch_wired_by_connections(self):
        # P2-1（v5）：双分支并存时以实际输出连接为准——main 出边 → execute 源；
        # 仅 ai_* 出边 → supplyData 源（消除取错源的静默错编译亚型）。
        both = {"code": {
            "execute": {"code": "return items.map((it) => ({ from: 'execute' }));"},
            "supplyData": {"code": "return new FakeEmbeddings();"},
        }}
        base_node = {"name": "Dual", "type": "@n8n/n8n-nodes-langchain.code",
                     "typeVersion": 1, "position": [0, 0], "parameters": both}
        # main 出边 -> execute 分支（root 节点：Main 输出存在才可能 execute，
        # 对齐 n8n Code.node.ts "The node does not have a Main output set"）
        wf_main = chain_workflow(
            [manual_trigger_node("Trigger"), json.loads(json.dumps(base_node)),
             code_node("Out", "return items;")],
            [("Trigger", "Dual"), ("Dual", "Out")],
        )
        ast_main = parse_workflow(wf_main)
        self.assertIn("from: 'execute'", ast_main.nodes["Dual"].js_contract.payload.source)
        # 仅 ai_embedding 出边 -> supplyData 分支
        wf_ai = {
            "nodes": [
                manual_trigger_node("Trigger"),
                {"name": "Agent", "type": "@n8n/n8n-nodes-langchain.agent",
                 "typeVersion": 1, "position": [0, 0], "parameters": {}},
                json.loads(json.dumps(base_node)),
            ],
            "connections": {
                "Trigger": {"main": [[{"node": "Agent", "type": "main", "index": 0}]]},
                "Dual": {"ai_embedding": [[{"node": "Agent", "type": "ai_embedding", "index": 0}]]},
            },
        }
        ast_ai = parse_workflow(wf_ai)
        self.assertIn("new FakeEmbeddings()", ast_ai.nodes["Dual"].js_contract.payload.source)

    def test_langchain_toolcode_statically_compiled(self):
        # P2-4（v5）：@n8n/n8n-nodes-langchain.toolCode 注册为 CODE kind——
        # 顶层 jsCode 取源（工具签名 (query) => result），走一等公民静态通道。
        wf = {
            "nodes": [
                manual_trigger_node("Trigger"),
                {"name": "Agent", "type": "@n8n/n8n-nodes-langchain.agent",
                 "typeVersion": 1, "position": [0, 0], "parameters": {}},
                tool_code_node("MyTool", "return query + '!';"),
            ],
            "connections": {
                "Trigger": {"main": [[{"node": "Agent", "type": "main", "index": 0}]]},
                "MyTool": {"ai_tool": [[{"node": "Agent", "type": "ai_tool", "index": 0}]]},
            },
        }
        ast = parse_workflow(wf)
        node = ast.nodes["MyTool"]
        self.assertIsInstance(node, CodeNode)  # 注册为 CODE -> CodeNode
        self.assertIsNotNone(node.js_contract)
        self.assertTrue(node.js_contract.ok)
        self.assertEqual(node.js_contract.payload.source, "return query + '!';")
        self.assertEqual(validate_workflow(ast), [])
        ir = compile_ast(ast, workflow_id="tc", version="1").to_dict()
        cfg = next(n["config"] for n in ir["nodes"] if n["key"] == "MyTool")
        self.assertIn("js", cfg)
        self.assertIn("js_ast", cfg)
        self.assertEqual(cfg["js"]["errors"], [])
        # ai_tool 子边守恒 + 纯 AI 子节点不入 main 拓扑
        self.assertEqual(len([c for c in ir["connections"]
                              if c.get("conn_type") == "ai_tool"]), 1)
        self.assertNotIn("MyTool", ir["execution_order"]["__root__"])

    def test_langchain_toolcode_bad_syntax_caught(self):
        # P2-4（v5）：toolCode JS 语法错误必须编译期抓出（此前落 GENERIC 静默）
        wf = {
            "nodes": [
                manual_trigger_node("Trigger"),
                {"name": "Agent", "type": "@n8n/n8n-nodes-langchain.agent",
                 "typeVersion": 1, "position": [0, 0], "parameters": {}},
                tool_code_node("MyTool", "return 1aaa;"),
            ],
            "connections": {
                "Trigger": {"main": [[{"node": "Agent", "type": "main", "index": 0}]]},
                "MyTool": {"ai_tool": [[{"node": "Agent", "type": "ai_tool", "index": 0}]]},
            },
        }
        ast = parse_workflow(wf)
        issues = validate_workflow(ast)
        self.assertTrue(any(i.code == "code_syntax_error" for i in issues),
                        f"expected code_syntax_error, got {[i.code for i in issues]}")

    def test_langchain_toolcode_python_unsupported(self):
        # P2-4（v5）：toolCode python 模式与 base.code 同纪律——显式 unsupported，
        # 不静默降级
        wf = {
            "nodes": [
                manual_trigger_node("Trigger"),
                {"name": "Agent", "type": "@n8n/n8n-nodes-langchain.agent",
                 "typeVersion": 1, "position": [0, 0], "parameters": {}},
                tool_code_node("MyTool", "return 'x'", language="python"),
            ],
            "connections": {
                "Trigger": {"main": [[{"node": "Agent", "type": "main", "index": 0}]]},
                "MyTool": {"ai_tool": [[{"node": "Agent", "type": "ai_tool", "index": 0}]]},
            },
        }
        from parser.node_adaptors import UnsupportedSourceError
        with self.assertRaises(UnsupportedSourceError):
            parse_workflow(wf)

    def test_settings_unknown_key_warns(self):
        # P2-2（v5）：未知 settings 键给出契约 warning（不阻断）；n8n REST
        # workflowSettings.yml additionalProperties:false —— deploy 前信号。
        wf = chain_workflow(
            [webhook_node("Webhook"), code_node("Transform", "return items;")],
            [("Webhook", "Transform")],
        )
        wf["settings"] = {"bogusKey": 1}
        compiled = compile_ast(parse_workflow(wf), workflow_id="s", version="1")
        self.assertTrue(any("bogusKey" in w and "unknown key" in w
                            for w in compiled.warnings))
        # warning 不阻断：产物可装载
        validate_typed_ir(compiled.document)

    def test_settings_enum_and_type_warns(self):
        # P2-2（v5）：已知键的非法枚举/类型同样 warning；合法值不 warning
        wf = chain_workflow(
            [webhook_node("Webhook"), code_node("Transform", "return items;")],
            [("Webhook", "Transform")],
        )
        wf["settings"] = {"saveDataErrorExecution": "sometimes",
                          "saveManualExecutions": "yes"}
        compiled = compile_ast(parse_workflow(wf), workflow_id="s", version="1")
        joined = "\n".join(compiled.warnings)
        self.assertIn("saveDataErrorExecution", joined)
        self.assertIn("expected boolean", joined)

        wf2 = chain_workflow(
            [webhook_node("Webhook"), code_node("Transform", "return items;")],
            [("Webhook", "Transform")],
        )
        wf2["settings"] = {"executionOrder": "v1", "saveManualExecutions": True}
        compiled2 = compile_ast(parse_workflow(wf2), workflow_id="s2", version="1")
        self.assertEqual(compiled2.warnings, [])

    def test_settings_non_object_rejected_at_parse(self):
        # P3-4（v5）：settings 非对象源 parse 期显式拒绝——不再产出自身 loader
        # 拒绝的 IR（曾透传后在 typed_ir 装载才炸）。
        wf = chain_workflow(
            [webhook_node("Webhook"), code_node("Transform", "return items;")],
            [("Webhook", "Transform")],
        )
        wf["settings"] = "v1"
        with self.assertRaises(ValueError):
            parse_workflow(wf)

    def test_settings_carried_in_ir_v2(self):
        # P3-8（v4）：IR v2 携带源 settings 原值（缺失 = 空 dict）
        wf = chain_workflow(
            [webhook_node("Webhook"), code_node("Transform", "return items;")],
            [("Webhook", "Transform")],
        )
        wf["settings"] = {"executionOrder": "v2", "saveManualExecutions": True}
        ir = compile_ast(parse_workflow(wf), workflow_id="s", version="1").to_dict()
        self.assertEqual(ir["workflow"]["settings"],
                         {"executionOrder": "v2", "saveManualExecutions": True})
        # 源无 settings -> 空 dict（decompile 侧回退 v1）
        ir2 = compile_ast(parse_workflow(chain_workflow(
            [webhook_node("Webhook"), code_node("Transform", "return items;")],
            [("Webhook", "Transform")],
        )), workflow_id="s2", version="1").to_dict()
        self.assertEqual(ir2["workflow"]["settings"], {})
        validate_typed_ir(ir2)

    def test_workflow_settings_must_be_object(self):
        # settings 必须是对象；非 dict 显式拒绝
        bad = json.loads(json.dumps(self.document))
        bad["workflow"]["settings"] = "v1"
        bad["digest"] = compute_typed_ir_digest(bad)
        with self.assertRaises(ValueError):
            validate_typed_ir(bad)

    def test_unknown_conn_type_rejected(self):
        # 连接类型白名单：main / ai_*；未知类型显式拒绝
        bad = json.loads(json.dumps(self.document))
        bad["connections"][0]["conn_type"] = "bogus"
        bad["digest"] = compute_typed_ir_digest(bad)
        with self.assertRaises(ValueError):
            validate_typed_ir(bad)

    def test_v1_document_without_conn_type_loads(self):
        # 兼容：真 v1 文档（connections 仅 main 边、无 conn_type、无
        # workflow.settings；execution_order 覆盖全部节点，AI 子节点含在内）
        # 仍可装载，conn_type 缺省 main。
        # P1-1（v5）：settings 是可选字段——真 v1 产物无此字段，不得被必填
        # 校验拒绝（曾因 _WORKFLOW_FIELDS 误列 required 而拒载）。
        v1 = json.loads(json.dumps(self.document))
        v1["connections"] = [
            c for c in v1["connections"] if c.get("conn_type", "main") == "main"
        ]
        for conn in v1["connections"]:
            conn.pop("conn_type", None)
        v1["workflow"].pop("settings", None)  # 真 v1 无 settings（P1-1）
        v1["execution_order"]["__root__"] = sorted(
            v1["execution_order"]["__root__"]
            + ["Gemini Chat Model", "Gemini Embeddings - Setup",
               "Gemini Embeddings - Query"]
        )
        v1["digest"] = compute_typed_ir_digest(v1)
        loaded = load_typed_ir_json(json.dumps(v1, ensure_ascii=False))
        self.assertTrue(all(c.get("conn_type", "main") == "main"
                            for c in loaded["connections"]))
        self.assertNotIn("settings", loaded["workflow"])  # 装载不注入 settings

    def test_manifest_resources(self):
        requires = self.document["manifest"]["requires"]
        model_ids = {m["id"] for m in requires["models"]}
        self.assertIn("models/gemini-1.5-pro", model_ids)
        self.assertIn("models/text-embedding-004", model_ids)
        store_ids = {v["id"] for v in requires["vector_stores"]}
        self.assertIn("regulatory_compliance_db", store_ids)
        webhook = requires["webhooks"][0]
        self.assertEqual(webhook["method"], "POST")
        self.assertEqual(webhook["path"], "compliance-check")

    def test_all_rag_code_nodes_compiled(self):
        code_keys = {
            n["key"] for n in self.document["nodes"] if n["config"].get("js")
        }
        self.assertEqual(
            code_keys,
            {"Build Compliance Report", "Extract Keywords for Vector Search",
             "Format Retrieved Regulations"},
        )
        for key in code_keys:
            node = next(n for n in self.document["nodes"] if n["key"] == key)
            self.assertEqual(node["config"]["js"]["errors"], [])
            self.assertIsNotNone(node["config"]["js_ast"])


if __name__ == "__main__":
    unittest.main()


class TestManualModeAndMultiOutput(unittest.TestCase):
    def test_no_trigger_workflow_compiles(self):
        # n8n 手动模式：无 trigger 工作流合法，entry_keys 为空
        wf = {
            "nodes": [
                {"name": "Set", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 0], "parameters": {}},
            ],
            "connections": {},
        }
        compiled = compile_ast(parse_workflow(wf), workflow_id="manual", version="1")
        self.assertEqual(compiled.document["workflow"]["entry_keys"], [])
        validate_typed_ir(compiled.document)
        verify_typed_ir_digest(compiled.document)

    def test_switch_workflow_compiles(self):
        from tests.helpers import switch_workflow
        compiled = compile_ast(parse_workflow(switch_workflow(4)), workflow_id="sw", version="1")
        doc = compiled.document
        validate_typed_ir(doc)
        verify_typed_ir_digest(doc)
        switch = compiled.find_node("Switch")
        self.assertEqual(
            sorted(switch["output_types"]),
            ["main_0", "main_1", "main_2", "main_3"],
        )
        ports = {c["from_port"] for c in doc["connections"] if c["from_node"] == "Switch"}
        self.assertEqual(ports, {"main_0", "main_1", "main_2", "main_3"})
        self.assertTrue(all("to_index" in c for c in doc["connections"]))
        self.assertTrue(all(isinstance(c["to_index"], int) for c in doc["connections"]))
        # 拓扑序合法
        order = doc["execution_order"][ROOT_SCOPE]
        positions = {key: i for i, key in enumerate(order)}
        for conn in doc["connections"]:
            self.assertLess(positions[conn["from_node"]], positions[conn["to_node"]])
