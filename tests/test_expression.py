"""表达式解析回归：$node / $env / $input / 复杂表达式 -> ParsedRef 分类。"""
import unittest

from parser.expression import (
    ExprKind,
    is_expression,
    parse_expression,
    parse_value,
)
from values.variable import GlobalVarType


class TestIsExpression(unittest.TestCase):
    def test_plain_value_not_expression(self):
        self.assertFalse(is_expression("hello"))
        self.assertFalse(is_expression(42))
        self.assertFalse(is_expression(None))

    def test_wrapped_is_expression(self):
        self.assertTrue(is_expression("={{ $json.x }}"))
        # 实现合约：is_expression 只认完整 ={{ ... }} 整串，
        # 内嵌 {{ }} 模板由 parse_value 提取（见 test_wrapped_parse_value）。
        self.assertFalse(is_expression("prefix {{ $env.X }} suffix"))
        self.assertFalse(is_expression("{{ $json.a }} + {{ $json.b }}"))

    def test_wrapped_parse_value(self):
        ref, dynamic = parse_value("prefix {{ $env.X }} suffix")
        self.assertTrue(dynamic)
        self.assertIsNotNone(ref)
        self.assertEqual(ref.kind, ExprKind.GLOBAL)
        self.assertEqual(ref.var_type, GlobalVarType.ENV)
        self.assertEqual(ref.path, ("X",))

        # 纯字面量 -> 非动态
        ref, dynamic = parse_value("plain text")
        self.assertFalse(dynamic)
        self.assertIsNone(ref)


class TestNodeRefs(unittest.TestCase):
    def test_node_bracket_with_json_path(self):
        ref = parse_expression('={{ $node["HTTP"].json.body.id }}')
        self.assertEqual(ref.kind, ExprKind.NODE)
        self.assertEqual(ref.node, "HTTP")
        self.assertEqual(ref.path, ("body", "id"))

    def test_node_dot_json_path(self):
        ref = parse_expression("={{ $node.Other.json.a.b }}")
        self.assertEqual(ref.kind, ExprKind.NODE)
        self.assertEqual(ref.node, "Other")
        self.assertEqual(ref.path, ("a", "b"))

    def test_node_output_alias(self):
        ref = parse_expression("={{ $node['X'].output.c }}")
        self.assertEqual(ref.kind, ExprKind.NODE)
        self.assertEqual(ref.path, ("c",))

    def test_node_plain(self):
        ref = parse_expression("={{ $node['Y'] }}")
        self.assertEqual(ref.kind, ExprKind.NODE)
        self.assertEqual(ref.node, "Y")
        self.assertEqual(ref.path, ())


class TestNodeAccessorDiscipline(unittest.TestCase):
    """$node["X"] 后只认数据访问器 json/output；其他属性不误绑（n8n 运行时
    求值失败，编译器标 UNKNOWN 保留原串，比错误绑定更安全）。"""

    def test_non_data_accessor_is_unknown(self):
        ref = parse_expression('={{ $node["X"].body.id }}')
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)
        self.assertEqual(ref.raw, '$node["X"].body.id')

    def test_json_accessor_without_rest_is_whole_object(self):
        ref = parse_expression('={{ $node["X"].json }}')
        self.assertEqual(ref.kind, ExprKind.NODE)
        self.assertEqual(ref.node, "X")
        self.assertEqual(ref.path, ())

    def test_dot_form_non_data_accessor_is_unknown(self):
        ref = parse_expression("={{ $node.X.param.foo }}")
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)


class TestGlobalRefs(unittest.TestCase):
    def test_env_dot(self):
        ref = parse_expression("={{ $env.API_KEY }}")
        self.assertEqual(ref.kind, ExprKind.GLOBAL)
        self.assertEqual(ref.var_type, GlobalVarType.ENV)
        self.assertEqual(ref.path, ("API_KEY",))

    def test_env_bracket(self):
        ref = parse_expression("={{ $env['DB_HOST'] }}")
        self.assertEqual(ref.kind, ExprKind.GLOBAL)
        self.assertEqual(ref.var_type, GlobalVarType.ENV)
        self.assertEqual(ref.path, ("DB_HOST",))

    def test_execution_bracket(self):
        ref = parse_expression("={{ $execution['id'] }}")
        self.assertEqual(ref.kind, ExprKind.GLOBAL)
        self.assertEqual(ref.var_type, GlobalVarType.EXECUTION)
        self.assertEqual(ref.path, ("id",))

    def test_now_plain(self):
        ref = parse_expression("={{ $now }}")
        self.assertEqual(ref.kind, ExprKind.GLOBAL)
        self.assertEqual(ref.var_type, GlobalVarType.NOW)
        self.assertEqual(ref.path, ())


class TestInputRefs(unittest.TestCase):
    def test_json_path(self):
        ref = parse_expression("={{ $json.user.name }}")
        self.assertEqual(ref.kind, ExprKind.INPUT)
        self.assertEqual(ref.path, ("user", "name"))

    def test_input_all_index(self):
        ref = parse_expression("={{ $input.all()[0].json.id }}")
        self.assertEqual(ref.kind, ExprKind.INPUT)
        self.assertEqual(ref.path, ("id",))

    def test_input_first(self):
        ref = parse_expression("={{ $input.first().json }}")
        self.assertEqual(ref.kind, ExprKind.INPUT)


class TestComplexExpressions(unittest.TestCase):
    def test_function_call_is_unknown(self):
        ref = parse_expression("={{ JSON.stringify($json) }}")
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)
        self.assertEqual(ref.raw, "JSON.stringify($json)")

    def test_arithmetic_is_unknown(self):
        ref = parse_expression("={{ $json.a + $json.b }}")
        self.assertEqual(ref.kind, ExprKind.UNKNOWN)

    def test_to_dict_roundtrip(self):
        ref = parse_expression('={{ $node["X"].json.a }}')
        d = ref.to_dict()
        self.assertEqual(d["kind"], "node")
        self.assertEqual(d["node"], "X")
        self.assertEqual(d["path"], ["a"])


if __name__ == "__main__":
    unittest.main()
