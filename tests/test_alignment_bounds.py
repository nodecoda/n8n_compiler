"""对拍边界回归：表达式访问器纪律、模板提取、Connection/IR 的 to_index 边界。

这些测试锁定「宁可 UNKNOWN 不误绑」的决策，防止将来有人"修复"导致
checker 字段误报或 IR 兼容性破坏。
"""
import json
import unittest

from ast_nodes.connection import Connection
from compiler.workflow import compile_ast
from parser.expression import ExprKind, parse_expression, parse_value
from parser.workflow import parse_workflow
from typed_ir import compute_typed_ir_digest, load_typed_ir_json, validate_typed_ir


# ---------------------------------------------------------------------------
# 表达式：$input 访问器纪律
# ---------------------------------------------------------------------------


class TestInputAccessorDiscipline(unittest.TestCase):
    """$input 后只认合法访问器（all()/first()/item）；未知方法不部分匹配。"""

    def test_input_item_extracts_path(self):
        ref = parse_expression("={{ $input.item.json.a }}")
        self.assertEqual(ref.kind, ExprKind.INPUT)
        self.assertEqual(ref.path, ("a",))

    def test_input_item_without_json(self):
        ref = parse_expression("={{ $input.item }}")
        self.assertEqual(ref.kind, ExprKind.INPUT)
        self.assertEqual(ref.path, ())

    def test_input_unknown_method_is_unknown(self):
        ref = parse_expression("={{ $input.custom() }}")
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)
        self.assertEqual(ref.raw, "$input.custom()")

    def test_input_bare_kept(self):
        ref = parse_expression("={{ $input }}")
        self.assertEqual(ref.kind, ExprKind.INPUT)
        self.assertEqual(ref.path, ())

    def test_input_all_first_preserved(self):
        self.assertEqual(parse_expression("={{ $input.all()[0].json.id }}").path, ("id",))
        ref = parse_expression("={{ $input.first().json }}")
        self.assertEqual(ref.kind, ExprKind.INPUT)
        self.assertEqual(ref.path, ())


# ---------------------------------------------------------------------------
# 表达式：$node 数据访问器扩展（binary/下标/参数访问器均不误绑）
# ---------------------------------------------------------------------------


class TestNodeAccessorExtended(unittest.TestCase):
    """$node["X"] 只认 json/output 数据访问器；binary/下标/参数访问器 -> UNKNOWN。

    .binary 虽是 n8n 合法数据访问器，但 checker 的字段校验基于 output_types
    的 json 形状，绑定 NODE path 会误报 source_field_missing -> 保持 UNKNOWN。
    """

    def test_binary_accessor_is_unknown(self):
        ref = parse_expression('={{ $node["X"].binary.data }}')
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)

    def test_json_array_index_is_unknown(self):
        ref = parse_expression('={{ $node["X"].json[0].id }}')
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)

    def test_params_accessor_is_unknown(self):
        ref = parse_expression('={{ $node["X"].params.foo }}')
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)

    def test_json_with_spaced_key_is_unknown(self):
        ref = parse_expression('={{ $node["X"].json["a b"] }}')
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)


# ---------------------------------------------------------------------------
# 表达式：parse_value 模板提取
# ---------------------------------------------------------------------------


class TestTemplateExtraction(unittest.TestCase):
    """内嵌 {{ }} 模板取首个引用意图（多段模板同）。"""

    def test_multi_segment_takes_first(self):
        ref, dyn = parse_value("{{ $json.a }} + {{ $json.b }}")
        self.assertTrue(dyn)
        self.assertEqual(ref.kind, ExprKind.INPUT)
        self.assertEqual(ref.path, ("a",))

    def test_embedded_node_ref(self):
        ref, dyn = parse_value("{{ $node['X'].json.a }}")
        self.assertTrue(dyn)
        self.assertEqual(ref.kind, ExprKind.NODE)
        self.assertEqual(ref.node, "X")
        self.assertEqual(ref.path, ("a",))

    def test_plain_text_not_dynamic(self):
        ref, dyn = parse_value("no template here")
        self.assertFalse(dyn)
        self.assertIsNone(ref)

    def test_expression_value_direct(self):
        ref, dyn = parse_value("={{ $json.a }}")
        self.assertTrue(dyn)
        self.assertEqual(ref.kind, ExprKind.INPUT)
        self.assertEqual(ref.path, ("a",))


# ---------------------------------------------------------------------------
# Connection.to_index 边界
# ---------------------------------------------------------------------------


class TestConnectionToIndexBounds(unittest.TestCase):
    def test_from_dict_string_index_coerced(self):
        conn = Connection.from_dict({"from_node": "A", "to_node": "B", "to_index": "1"})
        self.assertEqual(conn.to_index, 1)

    def test_identity_distinguishes_input_ports(self):
        c0 = Connection(from_node="A", from_port="main", to_node="M", to_index=0)
        c1 = Connection(from_node="A", from_port="main", to_node="M", to_index=1)
        self.assertNotEqual(c0.identity, c1.identity)
        self.assertEqual(len({c0.identity, c1.identity}), 2)

    def test_to_dict_round_trip_keeps_index(self):
        conn = Connection(from_node="A", from_port="main", to_node="B", to_index=2)
        self.assertEqual(Connection.from_dict(conn.to_dict()), conn)


# ---------------------------------------------------------------------------
# typed IR：to_index 校验 + 向后兼容
# ---------------------------------------------------------------------------


def _merge_workflow_doc():
    """Merge 双输入（index 0/1）编译后的 IR 文档。"""
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
    return compile_ast(parse_workflow(wf), workflow_id="merge", version="1").to_dict()


class TestTypedIRToIndex(unittest.TestCase):
    def test_merge_ir_round_trip_with_indexes(self):
        doc = _merge_workflow_doc()
        conns = {c["from_node"]: c for c in doc["connections"] if c["to_node"] == "Merge"}
        self.assertEqual(conns["A"]["to_index"], 0)
        self.assertEqual(conns["B"]["to_index"], 1)
        # load 往返 + digest 一致
        loaded = load_typed_ir_json(json.dumps(doc))
        self.assertEqual(loaded["connections"], doc["connections"])

    def test_legacy_ir_without_to_index_still_valid(self):
        # 向后兼容：旧 IR v1 无 to_index 字段（缺失 = 单输入 0）
        doc = _merge_workflow_doc()
        for c in doc["connections"]:
            c.pop("to_index", None)
        doc["digest"] = compute_typed_ir_digest(doc)
        validate_typed_ir(doc)  # verify_digest 默认 True
        self.assertTrue(all("to_index" not in c for c in doc["connections"]))

    def test_non_integer_to_index_rejected(self):
        for bad in ("0", 1.5, True, None, []):
            doc = _merge_workflow_doc()
            doc["connections"][0]["to_index"] = bad
            with self.assertRaises(ValueError, msg=f"to_index={bad!r} must be rejected"):
                validate_typed_ir(doc, verify_digest=False)

    def test_unknown_connection_field_rejected(self):
        doc = _merge_workflow_doc()
        doc["connections"][0]["bogus"] = 1
        with self.assertRaises(ValueError):
            validate_typed_ir(doc, verify_digest=False)


if __name__ == "__main__":
    unittest.main()
