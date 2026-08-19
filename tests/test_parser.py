"""parser 回归：端口映射（IF main_0/main_1）、ai_referenced、exit 收口、多输出端口。"""
import unittest

from ast_nodes.node_type import EXIT_NODE_KEY
from parser.workflow import parse_workflow
from tests.helpers import (
    chain_workflow,
    code_node,
    if_node,
    limit_node,
    make_workflow,
    set_node,
    switch_workflow,
    webhook_node,
)


def _mini_workflow(**overrides):
    wf = make_workflow(
        [webhook_node("Webhook"), if_node("IF"), set_node("Yes Branch"), limit_node("No Branch", x=1)],
        {
            "Webhook": {"main": [[{"node": "IF"}]]},
            "IF": {"main": [[{"node": "Yes Branch"}], [{"node": "No Branch"}]]},
        },
    )
    wf.update(overrides)
    return wf


class TestPortMapping(unittest.TestCase):
    def test_if_ports_are_named(self):
        ast = parse_workflow(_mini_workflow())
        ports = {(c.from_node, c.from_port) for c in ast.connections
                 if c.from_node == "IF"}
        self.assertIn(("IF", "main_0"), ports)
        self.assertIn(("IF", "main_1"), ports)

    def test_non_if_port_is_main(self):
        ast = parse_workflow(_mini_workflow())
        ports = {(c.from_node, c.from_port) for c in ast.connections
                 if c.from_node == "Webhook"}
        self.assertEqual(ports, {("Webhook", "main")})


class TestMergeMultiInputIndex(unittest.TestCase):
    """n8n 边 index = 目标输入端口索引（Merge 多输入语义，运行时按 index 等齐）。"""

    def test_merge_two_inputs_indexes_preserved(self):
        wf = {
            "nodes": [
                {"name": "A", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 0], "parameters": {}},
                {"name": "B", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 1], "parameters": {}},
                {"name": "Merge", "type": "n8n-nodes-base.merge", "typeVersion": 3,
                 "position": [0, 2], "parameters": {}},
            ],
            "connections": {
                "A": {"main": [[{"node": "Merge", "index": 0}]]},
                "B": {"main": [[{"node": "Merge", "index": 1}]]},
            },
        }
        ast = parse_workflow(wf)
        by_src = {(c.from_node, c.to_node): c for c in ast.connections}
        self.assertEqual(by_src[("A", "Merge")].to_index, 0)
        self.assertEqual(by_src[("B", "Merge")].to_index, 1)
        # 不同输入端口的边不视为重复
        self.assertNotEqual(by_src[("A", "Merge")].identity,
                            by_src[("B", "Merge")].identity)

    def test_default_index_is_zero(self):
        ast = parse_workflow(_mini_workflow())
        for c in ast.connections:
            self.assertEqual(c.to_index, 0)


class TestAiReferenced(unittest.TestCase):
    def test_ai_subnodes_recorded(self):
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
                "Agent": {
                    "main": [[{"node": "Trigger"}]],
                    "ai_languageModel": [[{"node": "Chat Model"}]],
                },
            },
        }
        ast = parse_workflow(wf)
        # n8n ai_* 连接方向 = 子节点 -> 主节点；两端都脱离 main 拓扑
        self.assertIn("Chat Model", ast.ai_referenced)
        self.assertIn("Agent", ast.ai_referenced)


class TestExitSynthesis(unittest.TestCase):
    def test_terminals_feed_exit(self):
        ast = parse_workflow(_mini_workflow())
        self.assertIn(EXIT_NODE_KEY, ast.nodes)
        exit_ins = {c.from_node for c in ast.connections if c.to_node == EXIT_NODE_KEY}
        # Yes/No Branch 均末端 -> 收口 exit
        self.assertEqual(exit_ins, {"Yes Branch", "No Branch"})

    def test_sink_not_connected_to_exit(self):
        wf = {
            "nodes": [
                {"name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1,
                 "position": [0, 1], "parameters": {"respondWith": "json"}},
            ],
            "connections": {"Webhook": {"main": [[{"node": "Respond"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ins = {c.from_node for c in ast.connections if c.to_node == EXIT_NODE_KEY}
        self.assertEqual(exit_ins, set())  # sink 显式输出，不接 exit


class TestRoundTrip(unittest.TestCase):
    def test_duplicate_node_rejected(self):
        wf = _mini_workflow()
        wf["nodes"].append(dict(wf["nodes"][0]))
        with self.assertRaises(ValueError):
            parse_workflow(wf)

    def test_unknown_edge_target_rejected(self):
        wf = _mini_workflow()
        wf["connections"]["Webhook"]["main"] = [[{"node": "Ghost"}]]
        with self.assertRaises(ValueError):
            parse_workflow(wf)


class TestCodeJsLiteralBinding(unittest.TestCase):
    def test_js_code_literal_braces_not_bound_as_expression(self):
        # P1-2 回归：jsCode 内的 {{ $node[...] }} 是字面量（n8n 运行时不对
        # Code 源码插值），不得被字符串级表达式提取误绑为依赖（曾导致
        # 合法工作流被 checker 以 "depends on missing node X" 拒绝）。
        src = '// {{ $node["X"].json.foo }}\nreturn { tpl: `{{ $node["X"].json.foo }}` };'
        wf = chain_workflow([webhook_node("W"), code_node("C", src)], [("W", "C")])
        ast = parse_workflow(wf)
        refs_to_x = [
            f for f in ast.nodes["C"].input_sources
            if f.source.ref is not None and f.source.ref.from_node_key == "X"
        ]
        self.assertEqual(refs_to_x, [])

class TestReservedNames(unittest.TestCase):
    def test_exit_reserved_name_rejected(self):
        # P2-11 回归：用户节点占用 __exit__ 保留名必须显式失败（曾静默覆盖
        # 造成假自环报错或节点丢失，诊断误导）
        wf = {
            "nodes": [
                {"name": "__exit__", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 0], "parameters": {}},
            ],
            "connections": {},
        }
        with self.assertRaises(ValueError) as ctx:
            parse_workflow(wf)
        self.assertIn("reserved", str(ctx.exception))



if __name__ == "__main__":
    unittest.main()


class TestMultiOutputPorts(unittest.TestCase):
    def test_switch_routes_named_main_i(self):
        ast = parse_workflow(switch_workflow(3))
        ports = sorted({c.from_port for c in ast.connections if c.from_node == "Switch"})
        self.assertEqual(ports, ["main_0", "main_1", "main_2"])

    def test_switch_ports_above_registry_declaration(self):
        # 注册表声明 4 路；实际 5 路 -> 宽容命名为 main_4
        ast = parse_workflow(switch_workflow(5))
        ports = sorted({c.from_port for c in ast.connections if c.from_node == "Switch"})
        self.assertEqual(ports, [f"main_{i}" for i in range(5)])

    def test_terminal_switch_exit_ports_follow_declared_routes(self):
        # 终端 Switch（无下游）：exit 收口按参数声明的路由数，不接注册表假端口
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3,
                 "position": [0, 1], "parameters": {"rules": {"values": [{}, {}, {}]}}},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, ["main_0", "main_1", "main_2"])

    def test_terminal_switch_five_routes_all_capped(self):
        # P1-2 回归：路由 > 注册表下限 4 时，main_4 不再漏收口
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3,
                 "position": [0, 1], "parameters": {"rules": {"values": [{}, {}, {}, {}, {}]}}},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, [f"main_{i}" for i in range(5)])

    def test_terminal_switch_no_rules_falls_back_to_registry(self):
        # 参数不可推导（老形状未知）-> 回退注册表 4
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 1,
                 "position": [0, 1], "parameters": {"conditions": []}},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, [f"main_{i}" for i in range(4)])

    def test_terminal_switch_expression_float_number_outputs(self):
        # P1-N1 边界：JSON 数值不分 int/float，numberOutputs=6.0 同样收口 6 端口
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3,
                 "position": [0, 1], "parameters": {
                     "mode": "expression", "numberOutputs": 6.0}},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, [f"main_{i}" for i in range(6)])

    def test_terminal_switch_expression_bool_falls_back_to_registry(self):
        # P1-N1 边界：numberOutputs=True（bool 是 int 子类）不可作端口数 ->
        # 回退注册表 4（不按 True=1 收口，也不炸）
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3,
                 "position": [0, 1], "parameters": {
                     "mode": "expression", "numberOutputs": True}},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, [f"main_{i}" for i in range(4)])

    def test_terminal_switch_expression_string_falls_back_to_registry(self):
        # P1-N1 边界：numberOutputs 非数值（字符串）-> 回退注册表 4
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3,
                 "position": [0, 1], "parameters": {
                     "mode": "expression", "numberOutputs": "6"}},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, [f"main_{i}" for i in range(4)])

    def test_terminal_switch_expression_missing_number_falls_back(self):
        # P1-N1 边界：expression 模式但缺 numberOutputs -> 回退注册表 4
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3,
                 "position": [0, 1], "parameters": {"mode": "expression"}},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, [f"main_{i}" for i in range(4)])

    def test_split_in_batches_two_ports(self):
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Loop", "type": "n8n-nodes-base.splitInBatches", "typeVersion": 2,
                 "position": [0, 1], "parameters": {}},
                {"name": "Body", "type": "n8n-nodes-base.set", "typeVersion": 3,
                 "position": [0, 2], "parameters": {}},
            ],
            "connections": {
                "W": {"main": [[{"node": "Loop"}]]},
                "Loop": {"main": [[{"node": "Body"}], [{"node": "Body"}]]},
            },
        }
        ast = parse_workflow(wf)
        ports = sorted({c.from_port for c in ast.connections if c.from_node == "Loop"})
        self.assertEqual(ports, ["main_0", "main_1"])  # loop / done

    def test_generic_multi_output_types(self):
        ast = parse_workflow(switch_workflow(2))
        node = ast.nodes["Switch"]
        self.assertEqual(
            sorted(node.output_types),
            ["main_0", "main_1", "main_2", "main_3"],  # 注册表声明 4 端口
        )

    def test_terminal_switch_expression_mode_ports_capped(self):
        # P1-N1 回归：Switch V3 mode='expression' 输出端口数 = numberOutputs
        # （SwitchV3.node.ts:24-29），与 rules 无关；终端场景 main_0..main_5
        # 必须全部被 exit 收口（否则 IR 消费方静默丢输出语义）。
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
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, [f"main_{i}" for i in range(6)])

    def test_terminal_switch_fallback_extra_ports_capped(self):
        # P1-1 回归：Switch V3 fallbackOutput 'extra' 在 rules.length 处加一
        # 输出端口；终端场景该端口必须被 exit 收口（否则未匹配项静默丢失）。
        wf = {
            "nodes": [
                {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                 "position": [0, 0], "parameters": {}},
                {"name": "Switch", "type": "n8n-nodes-base.switch", "typeVersion": 3,
                 "position": [0, 1], "parameters": {
                     "rules": {"values": [{}, {}]},
                     "options": {"fallbackOutput": "extra"},
                 }},
            ],
            "connections": {"W": {"main": [[{"node": "Switch"}]]}},
        }
        ast = parse_workflow(wf)
        exit_ports = sorted({c.from_port for c in ast.connections
                             if c.from_node == "Switch" and c.to_node == EXIT_NODE_KEY})
        self.assertEqual(exit_ports, ["main_0", "main_1", "main_2"])

    def test_exit_synthesis_uses_actual_ports(self):
        # Switch main_1 未连（丢弃分支）：exit 不产生假端口收口
        wf = switch_workflow(2, Switch={"main": [[{"node": "Target0"}], []]})
        ast = parse_workflow(wf)
        exit_ins = {(c.from_node, c.from_port) for c in ast.connections
                    if c.to_node == EXIT_NODE_KEY}
        # Target0 是末端 -> 收口；Switch 的 main_1 未连分支丢弃，不连 exit
        self.assertIn(("Target0", "main"), exit_ins)
        self.assertNotIn(("Switch", "main_1"), exit_ins)
