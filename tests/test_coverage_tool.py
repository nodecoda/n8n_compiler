"""tests/coverage.py 口径回归（P2-N3）。

覆盖率门禁的口径判断（_is_project_module）是治理关键路径：runtime/ 是
部署闭环最险层，必须计入；测试与第三方不计入。固化防止未来口径回退。
"""
import unittest
from pathlib import Path

import tests.coverage as cov  # noqa: E402


class TestIsProjectModule(unittest.TestCase):
    """_is_project_module 口径：runtime 计入、tests 不计、仓外不计。"""

    def test_runtime_modules_counted(self):
        # P2-N3：runtime/ 纳入后，decompile/deploy 必须进覆盖率
        self.assertTrue(cov._is_project_module(str(cov.ROOT / "runtime" / "decompile.py")))
        self.assertTrue(cov._is_project_module(str(cov.ROOT / "runtime" / "deploy.py")))
        self.assertTrue(cov._is_project_module(str(cov.ROOT / "runtime" / "__init__.py")))

    def test_core_packages_counted(self):
        self.assertTrue(cov._is_project_module(str(cov.ROOT / "parser" / "workflow.py")))
        self.assertTrue(cov._is_project_module(str(cov.ROOT / "typed_ir.py")))

    def test_tests_not_counted(self):
        self.assertFalse(cov._is_project_module(str(cov.ROOT / "tests" / "test_parser.py")))
        self.assertFalse(cov._is_project_module(str(cov.ROOT / "tests" / "coverage.py")))

    def test_outside_repo_not_counted(self):
        self.assertFalse(cov._is_project_module("/etc/passwd"))
        self.assertFalse(cov._is_project_module(str(Path.home() / "x.py")))


if __name__ == "__main__":
    unittest.main()
