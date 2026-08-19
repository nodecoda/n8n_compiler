"""scripts/execute_matrix.py 回归：A2 矩阵的本地可测部分。

覆盖：场景结构不变量（起始 Trigger、连接端口不越界）、产物可编译可反编译
（id 存在、含 Trigger）、assert 对 n8n CLI 日志前缀的解析与 PASS/FAIL 判定。
远端真实执行由 scripts/execute_matrix.py 手动复跑（见 ana-docs/decompile-roundtrip.md）。
"""
import json
import tempfile
import unittest
from pathlib import Path

from scripts.execute_matrix import (
    OUT_NODE,
    _compile_scenario,
    _scenarios,
    command_assert,
)

# n8n CLI --rawOutput 仍会把启动日志写到 stdout，JSON 从首个 "{" 行开始
LOG_PREFIX = (
    'Failed to load Custom API options for the node "n8n-nodes-base.confluence": Unknown credential name "confluenceCloudOAuth2Api"\n'
    'n8n Task Broker ready on 127.0.0.1, port 5679\n'
    'Registered runner "JS Task Runner" (abc123) \n'
)


def _result_payload(run_data: dict, status: str = "success") -> str:
    """构造 n8n execute --rawOutput 的 stdout（日志前缀 + 结果 JSON）。"""
    body = {
        "data": {"resultData": {"runData": run_data}},
        "status": status,
        "finished": status == "success",
    }
    return LOG_PREFIX + json.dumps(body)


def _run_data(node: str, items: list[dict]) -> dict:
    return {node: [{"data": {"main": [[{"json": it} for it in items]]}}]}


class TestScenarioInvariants(unittest.TestCase):
    """每个场景的图结构不变量：起始 Trigger、边端口在源节点输出范围内。"""

    def test_all_scenarios_have_starting_trigger(self):
        for scenario in _scenarios():
            types = [n["type"] for n in scenario["wf"]["nodes"]]
            self.assertIn("n8n-nodes-base.manualTrigger", types,
                          f"{scenario['name']}: 缺少 Manual Trigger（execute CLI 需要）")

    def test_all_scenarios_compile_and_decompile(self):
        for scenario in _scenarios():
            wf = _compile_scenario(scenario, f"mx-{scenario['name']}")
            self.assertEqual(wf["id"], f"mx-{scenario['name']}")
            self.assertIn("n8n-nodes-base.manualTrigger", [n["type"] for n in wf["nodes"]])
            # 反编译后连接指向的节点必须存在
            names = {n["name"] for n in wf["nodes"]}
            for source, ports in wf["connections"].items():
                for branch in ports["main"]:
                    for conn in branch:
                        self.assertIn(conn["node"], names,
                                      f"{scenario['name']}: 边 {source}->{conn['node']} 目标不存在")

    def test_scenario_expect_nodes_exist(self):
        for scenario in _scenarios():
            names = {n["name"] for n in scenario["wf"]["nodes"]}
            expect_node = scenario.get("expect_node", OUT_NODE)
            self.assertIn(expect_node, names, f"{scenario['name']}: 断言节点 {expect_node} 不存在")


class TestCommandAssert(unittest.TestCase):
    def test_parses_log_prefix_and_passes(self):
        scenario = {"name": "code_chain", "expect_node": "Out", "expect": {"x": 10}}
        with tempfile.TemporaryDirectory() as td:
            Path(td, "code_chain.json").write_text(
                _result_payload(_run_data("Out", [{"x": 10}])), encoding="utf-8")
            code = self._run_assert(td, scenario)
        self.assertEqual(code, 0)

    def _run_assert(self, td, scenario):
        from unittest import mock
        with mock.patch("scripts.execute_matrix._scenarios", return_value=[scenario]):
            return command_assert(Path(td))

    def test_reports_mismatch_as_failure(self):
        scenario = {"name": "code_chain", "expect_node": "Out", "expect": {"x": 99}}
        with tempfile.TemporaryDirectory() as td:
            Path(td, "code_chain.json").write_text(
                _result_payload(_run_data("Out", [{"x": 10}])), encoding="utf-8")
            code = self._run_assert(td, scenario)
        self.assertEqual(code, 1)

    def test_reports_error_status_as_failure(self):
        scenario = {"name": "code_chain", "expect_node": "Out", "expect": {"x": 10}}
        with tempfile.TemporaryDirectory() as td:
            Path(td, "code_chain.json").write_text(
                _result_payload(_run_data("Out", []), status="error"), encoding="utf-8")
            code = self._run_assert(td, scenario)
        self.assertEqual(code, 1)

    def test_set_replace_semantics_assertion_distinguishes_merge(self):
        # P2-N4：强化后的断言必须能区分替换/合并——若 n8n 实际是合并语义
        # （x 保留，has_x=True），断言必须 FAIL；替换语义（has_x=False）才 PASS。
        scenario = {"name": "set_assignments", "expect_node": "Out",
                    "expect": {"y": 5, "has_x": False}}
        # 合并语义产物（x 保留）-> FAIL
        with tempfile.TemporaryDirectory() as td:
            Path(td, "set_assignments.json").write_text(
                _result_payload(_run_data("Out", [{"y": 5, "has_x": True}])),
                encoding="utf-8")
            code = self._run_assert(td, scenario)
        self.assertEqual(code, 1, "合并语义（has_x=True）必须被判失败")
        # 替换语义产物 -> PASS
        with tempfile.TemporaryDirectory() as td:
            Path(td, "set_assignments.json").write_text(
                _result_payload(_run_data("Out", [{"y": 5, "has_x": False}])),
                encoding="utf-8")
            code = self._run_assert(td, scenario)
        self.assertEqual(code, 0, "替换语义（has_x=False）应通过")

    def test_missing_result_is_failure(self):
        scenario = {"name": "missing_scene", "expect_node": "Out", "expect": {}}
        with tempfile.TemporaryDirectory() as td:
            code = self._run_assert(td, scenario)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
