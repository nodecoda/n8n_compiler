"""CLI 入口回归：check / compile 子命令的端到端行为。"""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import cli  # noqa: E402
from code.js_parser import JSInfraError  # noqa: E402
from tests.helpers import rag_fixture  # noqa: E402
from typed_ir import load_typed_ir_json, verify_typed_ir_digest  # noqa: E402


def _run_cli(argv: list[str]) -> tuple[int, str]:
    """跑 CLI，捕获 stdout（check 的 issue JSON 不污染测试日志）。"""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def _write(tmp: Path, name: str, data: dict) -> Path:
    p = tmp / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


class TestCliCheck(unittest.TestCase):
    def test_check_ok_rag_fixture(self):
        if not rag_fixture().exists():
            self.skipTest(f"RAG fixture not present at {rag_fixture()} (set N8N_REPO)")
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "wf.json"
            src.write_bytes(rag_fixture().read_bytes())
            rc, _out = _run_cli(["check", str(src)])
            self.assertEqual(rc, 0)

    def test_check_reports_issues_exit_1(self):
        with tempfile.TemporaryDirectory() as td:
            src = _write(Path(td), "cyclic.json", {
                "nodes": [
                    {"name": "A", "type": "n8n-nodes-base.set", "typeVersion": 3,
                     "position": [0, 0], "parameters": {}},
                    {"name": "B", "type": "n8n-nodes-base.set", "typeVersion": 3,
                     "position": [0, 1], "parameters": {}},
                ],
                "connections": {
                    "A": {"main": [[{"node": "B"}]]},
                    "B": {"main": [[{"node": "A"}]]},
                },
            })
            rc, out = _run_cli(["check", str(src)])
            self.assertEqual(rc, 1)
            self.assertIn("cycle_detected", out)

    def test_check_bad_json_exit_2(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "bad.json"
            src.write_text("{ not json", encoding="utf-8")
            rc, _out = _run_cli(["check", str(src)])
            self.assertEqual(rc, 2)


class TestCliCompile(unittest.TestCase):
    def test_compile_produces_loadable_ir(self):
        with tempfile.TemporaryDirectory() as td:
            src = _write(Path(td), "mini.json", {
                "nodes": [
                    {"name": "W", "type": "n8n-nodes-base.webhook", "typeVersion": 2,
                     "position": [0, 0], "parameters": {}},
                    {"name": "Code", "type": "n8n-nodes-base.code", "typeVersion": 2,
                     "position": [0, 1],
                     "parameters": {"mode": "runOnceForAllItems",
                                    "jsCode": "return items.map((it) => ({ v: it.json.v }));"}},
                ],
                "connections": {"W": {"main": [[{"node": "Code"}]]}},
            })
            out = Path(td) / "out.ir.json"
            rc, _out = _run_cli(["compile", str(src), "-o", str(out),
                                 "--workflow-id", "mini", "--version", "1.0"])
            self.assertEqual(rc, 0)
            doc = load_typed_ir_json(out.read_text(encoding="utf-8"))
            verify_typed_ir_digest(doc)
            self.assertEqual(doc["workflow"]["id"], "mini")
            self.assertEqual(doc["workflow"]["entry_keys"], ["W"])

    def test_compile_stdout(self):
        with tempfile.TemporaryDirectory() as td:
            src = _write(Path(td), "mini.json", {
                "nodes": [
                    {"name": "W", "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
                     "position": [0, 0], "parameters": {}},
                ],
                "connections": {},
            })
            rc, out = _run_cli(["compile", str(src)])
            self.assertEqual(rc, 0)
            doc = load_typed_ir_json(out)
            self.assertEqual(doc["workflow"]["entry_keys"], ["W"])

class TestCliExitCodes(unittest.TestCase):
    """P2-7 回归：退出码约定 0/1/2/3（成功/校验失败/输入错误/基础设施错误）。"""

    def test_compile_validation_error_exit_1(self):
        # validator 拒绝（环）-> WorkflowValidationError -> 1（源错误）
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            src = _write(Path(td), "cyclic.json", {
                "nodes": [
                    {"name": "A", "type": "n8n-nodes-base.set", "typeVersion": 3,
                     "position": [0, 0], "parameters": {}},
                    {"name": "B", "type": "n8n-nodes-base.set", "typeVersion": 3,
                     "position": [0, 1], "parameters": {}},
                ],
                "connections": {
                    "A": {"main": [[{"node": "B"}]]},
                    "B": {"main": [[{"node": "A"}]]},
                },
            })
            rc, _ = _run_cli(["compile", str(src)])
            self.assertEqual(rc, 1)

    def test_infrastructure_error_exit_3(self):
        # acorn 桥不可用 -> JSInfraError -> 3（与源错误区分，脚本可据此判断环境）
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            src = _write(Path(td), "mini.json", {"nodes": [], "connections": {}})
            with mock.patch("cli.command_compile", side_effect=JSInfraError("bridge down")):
                rc, _ = _run_cli(["compile", str(src)])
            self.assertEqual(rc, 3)

    def test_python_mode_code_exit_1(self):
        # P3-10 回归：Python 模式 Code 是「合法但 v1 不支持」的源级问题，
        # 映射退出码 1（与畸形输入 2、基础设施 3 区分），stderr 可辨
        with tempfile.TemporaryDirectory() as td:
            src = _write(Path(td), "py.json", {
                "nodes": [{"name": "C", "type": "n8n-nodes-base.code", "typeVersion": 2,
                           "position": [0, 0], "parameters": {
                               "language": "python", "pythonCode": "print(1)"}}],
                "connections": {},
            })
            rc, _ = _run_cli(["check", str(src)])
            self.assertEqual(rc, 1)

    def test_nan_input_rejected_exit_2(self):
        # P2-9 回归：NaN 参数在输入侧前置拒绝（parse_constant），不再编译后期炸
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "nan.json"
            src.write_text(json.dumps({
                "nodes": [{"name": "W", "type": "n8n-nodes-base.set", "typeVersion": 3,
                           "position": [0, 0],
                           "parameters": {"assignments": {"values": [
                               {"name": "x", "value": float("nan"), "type": "number"}]}}}],
                "connections": {},
            }, ensure_ascii=False), encoding="utf-8")
            rc, _ = _run_cli(["compile", str(src)])
            self.assertEqual(rc, 2)



if __name__ == "__main__":
    unittest.main()
