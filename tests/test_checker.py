"""checker 回归：三类误报修复 + 结构错误仍检出 + n8n 语义适配。"""
import json
import unittest

from checker.validator import (  # noqa: E402
    detect_cycles,
    validate_connections,
    validate_syntax,
    validate_workflow,
)
from parser.workflow import parse_workflow  # noqa: E402
from tests.helpers import (  # noqa: E402
    code_node,
    make_workflow,
    mini_webhook_workflow,
    rag_fixture,
    set_node,
    webhook_node,
)


def _parse(name: str, **overrides):
    wf = mini_webhook_workflow()
    wf["name"] = name
    for key, value in overrides.items():
        wf[key] = value
    return parse_workflow(wf)


class TestPortFix(unittest.TestCase):
    def test_if_single_output_port_is_legal(self):
        ast = _parse("port")
        issues = validate_workflow(ast)
        codes = [i.code for i in issues]
        self.assertNotIn("unknown_output_port", codes)
        self.assertNotIn("node_not_connected", codes)
        self.assertEqual(issues, [])


class TestShapeUnknownFix(unittest.TestCase):
    def test_code_list_output_field_access_passes(self):
        # Code 节点 return items.map(...) -> LIST 形状；下游引用 .prompt 不应报缺字段
        wf = {
            "nodes": [
                {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                code_node("Transform", "return items.map((it) => ({ prompt: it.text }));"),
                {"name": "Consumer", "type": "@n8n/n8n-nodes-langchain.chainLlm",
                 "typeVersion": 1, "position": [0, 2],
                 "parameters": {"text": "={{ $json.prompt }}"}},
            ],
            "connections": {
                "Webhook": {"main": [[{"node": "Transform"}]]},
                "Transform": {"main": [[{"node": "Consumer"}]]},
            },
        }
        ast = parse_workflow(wf)
        issues = validate_workflow(ast)
        codes = [i.code for i in issues]
        self.assertNotIn("source_field_missing", codes)
        self.assertEqual(issues, [])


class TestAiReferencedFix(unittest.TestCase):
    def test_ai_subnodes_not_isolated(self):
        wf = {
            "nodes": [
                {"name": "Trigger", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
                 "position": [0, 0], "parameters": {}},
                {"name": "Agent", "type": "@n8n/n8n-nodes-langchain.agent", "typeVersion": 1,
                 "position": [0, 1], "parameters": {}},
                {"name": "Chat Model", "type": "@n8n/n8n-nodes-langchain.lmChatOpenAi",
                 "typeVersion": 1, "position": [0, 2], "parameters": {}},
            ],
            "connections": {
                "Trigger": {"main": [[{"node": "Agent"}]]},
                "Agent": {"main": [[{"node": "Trigger"}]],
                          "ai_languageModel": [[{"node": "Chat Model"}]]},
            },
        }
        ast = parse_workflow(wf)
        issues = validate_workflow(ast)
        codes = [i.code for i in issues]
        self.assertNotIn("node_not_connected", codes)


class TestRealErrorsStillDetected(unittest.TestCase):
    def test_duplicate_edge(self):
        wf = {
            "nodes": [
                {"name": "A", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
                 "position": [0, 0], "parameters": {}},
                {"name": "B", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1], "parameters": {}},
            ],
            "connections": {"A": {"main": [[{"node": "B"}, {"node": "B"}]]}},
        }
        ast = parse_workflow(wf)
        issues = validate_syntax(ast)
        self.assertTrue(any(i.code == "duplicate_edge" for i in issues))

    def test_cycle_detected(self):
        wf = {
            "nodes": [
                {"name": "A", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 0], "parameters": {}},
                {"name": "B", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1], "parameters": {}},
            ],
            "connections": {"A": {"main": [[{"node": "B"}]]},
                            "B": {"main": [[{"node": "A"}]]}},
        }
        ast = parse_workflow(wf)
        issues = detect_cycles(ast)
        self.assertTrue(any(i.code == "cycle_detected" for i in issues))

    def test_isolated_node_is_legal(self):
        # n8n 适配点：编辑器允许孤立节点（不参与执行），保存零校验
        wf = {
            "nodes": [
                {"name": "A", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
                 "position": [0, 0], "parameters": {}},
                {"name": "B", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1], "parameters": {}},
                {"name": "Lonely", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [5, 5], "parameters": {}},
            ],
            "connections": {"A": {"main": [[{"node": "B"}]]}},
        }
        ast = parse_workflow(wf)
        issues = validate_workflow(ast)
        self.assertFalse(any(i.code == "node_not_connected" for i in issues))

    def test_trigger_without_outgoing_is_legal(self):
        # n8n 适配点：trigger 无出边合法（pinned/测试场景）
        wf = {
            "nodes": [
                {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
            ],
            "connections": {},
        }
        ast = parse_workflow(wf)
        issues = validate_workflow(ast)
        self.assertFalse(any(i.code == "trigger_not_connected" for i in issues))


class TestRagFixtureEndToEnd(unittest.TestCase):
    """完整 RAG fixture：合法工作流必须 0 issues（回归基线）。"""

    def test_zero_issues(self):
        fixture = rag_fixture()
        if not fixture.exists():
            self.skipTest(f"RAG fixture not present at {fixture} (set N8N_REPO)")
        data = json.loads(fixture.read_text(encoding="utf-8"))
        ast = parse_workflow(data.get("workflow", data))
        issues = validate_workflow(ast)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()


class TestPortShapeValidation(unittest.TestCase):
    """unknown_output_port 只查端口名形状（main | main_N），存在性放行。"""

    @staticmethod
    def _manual_ast(from_port: str):
        from ast_nodes.connection import Connection
        from ast_nodes.node_decls import ExitNode, IfNode
        from ast_nodes.node_type import EXIT_NODE_KEY
        from ast_nodes.nodes import WorkflowAST
        ifn = IfNode(key="IF", name="IF", n8n_type="n8n-nodes-base.if")
        exitn = ExitNode(key=EXIT_NODE_KEY, name="__exit__")
        return WorkflowAST(
            nodes={"IF": ifn, EXIT_NODE_KEY: exitn},
            connections=[Connection(from_node="IF", from_port=from_port, to_node=EXIT_NODE_KEY)],
        )

    def test_malformed_port_name_rejected(self):
        issues = validate_connections(self._manual_ast("mian_0"))
        self.assertTrue(any(i.code == "unknown_output_port" for i in issues))

    def test_any_main_n_shape_legal(self):
        issues = validate_connections(self._manual_ast("main_5"))
        self.assertEqual([i.code for i in issues], [])


class TestSwitchWorkflow(unittest.TestCase):
    def test_switch_full_workflow_zero_issues(self):
        from tests.helpers import switch_workflow
        ast = parse_workflow(switch_workflow(3))
        issues = validate_workflow(ast)
        self.assertEqual(issues, [])


class TestGlobalVarPathPolicy(unittest.TestCase):
    def test_env_empty_path_reported_now_legal(self):
        # $env 要求路径；$now/$execution 等对象型允许整对象引用
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {"path": "in", "httpMethod": "POST"}},
                {"name": "Set", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1],
                 "parameters": {"assignments": {"assignments": [
                     {"id": "e", "name": "envRef", "value": "={{ $env }}"},
                     {"id": "n", "name": "nowRef", "value": "={{ $now }}"},
                     {"id": "x", "name": "execRef", "value": "={{ $execution }}"},
                 ]}}},
            ],
            "connections": {"W": {"main": [[{"node": "Set"}]]}},
        }
        ast = parse_workflow(wf)
        issues = validate_workflow(ast)
        gvpe = [i for i in issues if i.code == "global_variable_path_empty"]
        self.assertEqual(len(gvpe), 1)  # 只有 $env 报
        self.assertEqual(gvpe[0].node_id, "Set")
