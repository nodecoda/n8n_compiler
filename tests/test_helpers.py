"""tests/helpers.py 基础设施回归：分层 skip 守卫行为 + fixture 契约。

守卫是测试治理的关键路径（P2-N2：缺依赖必须 skip 而非 error），守卫自身
必须有测试；fixture 形状契约（P3-6：Set assignments 形状要求 typeVersion>=3.3）
也在此固化，防止未来矩阵场景复用错误形状静默错执行。
"""
import os
import unittest
from unittest import mock

from code.js_parser import JSInfraError
from tests.helpers import set_node, skip_unless_node


def _run_decorated(dummy_cls) -> unittest.TestResult:
    """跑被守卫装饰的类，返回 TestResult（断言 skip/error/pass 计数）。"""
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(dummy_cls)
    result = unittest.TestResult()
    suite.run(result)
    return result


class TestSkipUnlessNode(unittest.TestCase):
    """守卫三分支：JSInfraError -> skip；不可执行 -> skip；正常 -> 运行。

    用环境变量注入驱动（守卫闭包在装饰时已绑定 find_node，mock 模块属性
    不生效）；与实测 `NODE=/nonexistent/node` 走同一条真实路径。
    """

    def _with_node_env(self, env: dict):
        return mock.patch.dict(os.environ, env, clear=True)

    def test_missing_node_bridge_error_skips(self):
        # 无 NODE 环境变量且 PATH 无 node -> find_node 抛 JSInfraError -> skip
        @skip_unless_node
        class Dummy(unittest.TestCase):
            def test_placeholder(self):
                pass

        with self._with_node_env({}):
            with mock.patch("shutil.which", return_value=None):
                result = _run_decorated(Dummy)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])

    def test_non_executable_node_skips(self):
        # NODE 指向不存在的路径：find_node 返回但不可执行 -> skip
        @skip_unless_node
        class Dummy(unittest.TestCase):
            def test_placeholder(self):
                pass

        with self._with_node_env({"NODE": "/nonexistent/node-xyz"}):
            result = _run_decorated(Dummy)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.errors, [])

    def test_available_node_runs(self):
        # 正常路径：NODE 指向真实可执行文件 -> 守卫放行，测试体真正执行
        ran = []

        @skip_unless_node
        class Dummy(unittest.TestCase):
            def test_placeholder(self):
                ran.append(True)

        with self._with_node_env({"NODE": os.sys.executable}):
            result = _run_decorated(Dummy)
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(result.skipped), 0)
        self.assertEqual(result.errors, [])
        self.assertEqual(ran, [True])


class TestSetNodeFixtureContract(unittest.TestCase):
    """P3-6：set_node fixture 必须满足 n8n Set 节点形状契约。"""

    def test_type_version_at_least_3_3(self):
        # assignments.assignments 形状只有 typeVersion >= 3.3 才被 n8n 读取
        # （SetV2/manual.mode.ts: typeVersion<3.3 走旧 fields.values 并静默忽略）
        node = set_node("S")
        self.assertGreaterEqual(node["typeVersion"], 3.3)

    def test_assignments_shape(self):
        node = set_node("S")
        self.assertIsInstance(node["parameters"]["assignments"], dict)
        self.assertIn("assignments", node["parameters"]["assignments"])


if __name__ == "__main__":
    unittest.main()
