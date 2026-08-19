"""runtime.deploy 回归：IR -> n8n REST API 部署（urlopen mock，无真实网络）。

覆盖：请求形状（endpoint/method/header/body=decompile 产物）、成功返回、
HTTP 错误/网络错误/非 JSON 响应显式失败、篡改 IR digest 拒绝、JSON 入口。
"""
import io
import json
import unittest
import urllib.error
from unittest import mock

from compiler.workflow import compile_ast
from parser.workflow import parse_workflow
from runtime.deploy import deploy_ir_json, deploy_to_n8n
from tests.helpers import mini_webhook_workflow


def _compile(wf: dict) -> dict:
    return compile_ast(parse_workflow(wf), workflow_id="wf-1", version="1").to_dict()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(urlopen_mock: mock.Mock, created: dict | None = None) -> dict:
    """抓取 urlopen 收到的请求参数。"""
    call = urlopen_mock.call_args
    request = call[0][0] if call.args else call.kwargs.get("request")
    return {
        "url": request.full_url,
        "method": request.get_method(),
        # urllib 会小写化 header 名，统一规范化便于断言
        "headers": {k.lower(): v for k, v in request.header_items()},
        "body": json.loads(request.data),
    }


class TestDeployToN8n(unittest.TestCase):
    def test_posts_decompiled_workflow_shape(self):
        ir = _compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(json.dumps({"id": "wf-9", "name": "mini"}).encode())
            created = deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="secret-key")
        self.assertEqual(created["id"], "wf-9")
        captured = _capture(urlopen_mock)
        self.assertEqual(captured["url"], "https://n8n.example.com/api/v1/workflows")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["headers"]["content-type"], "application/json")
        self.assertEqual(captured["headers"]["x-n8n-api-key"], "secret-key")
        # body 是反编译产物：nodes + connections 齐全
        self.assertTrue(captured["body"]["nodes"])
        self.assertIn("connections", captured["body"])
        # P2 真实实例回归：REST API 要求 settings 字段存在（缺失 -> 400），
        # envelope 必须携带编辑器默认的 executionOrder: v1
        self.assertEqual(captured["body"]["settings"], {"executionOrder": "v1"})
        # P2 真实实例回归：REST workflowCreate schema 将 id 标为 readOnly
        # （携带 -> 400），部署 payload 不得包含 id（服务端生成）
        self.assertNotIn("id", captured["body"])

    def test_name_from_workflow_id(self):
        # 未传 name -> 用 IR workflow.id；decompile 默认 name 不生效
        ir = _compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(b'{"id":"wf-1"}')
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k")
        self.assertEqual(_capture(urlopen_mock)["body"]["name"], "wf-1")

    def test_explicit_name_wins(self):
        ir = _compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(b'{"id":"wf-1"}')
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k", name="my-wf")
        self.assertEqual(_capture(urlopen_mock)["body"]["name"], "my-wf")

    def test_http_error_raises_with_detail(self):
        ir = _compile(mini_webhook_workflow())
        http_err = urllib.error.HTTPError(
            "https://n8n.example.com/api/v1/workflows", 401, "Unauthorized",
            {}, io.BytesIO(b'{"message":"invalid api key"}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=http_err), \
                self.assertRaises(ValueError) as ctx:
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="bad")
        self.assertIn("401", str(ctx.exception))
        self.assertIn("invalid api key", str(ctx.exception))

    def test_network_error_raises(self):
        ir = _compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")), \
                self.assertRaises(ValueError) as ctx:
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k")
        self.assertIn("network", str(ctx.exception))

    def test_non_json_response_raises(self):
        ir = _compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(b"<html>oops</html>")
            with self.assertRaises(ValueError) as ctx:
                deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k")
        self.assertIn("non-JSON", str(ctx.exception))

    def test_tampered_ir_rejected(self):
        # digest 防篡改：改节点名 -> 部署前显式失败，不发出请求
        ir = _compile(mini_webhook_workflow())
        ir["nodes"][0]["name"] = "Hacked"
        with mock.patch("urllib.request.urlopen") as urlopen_mock, \
                self.assertRaises(ValueError):
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k")
        urlopen_mock.assert_not_called()


class TestDeployUpsert(unittest.TestCase):
    """P2-2：upsert 模式（GET ?name -> PATCH/POST）+ P3-3 响应 id 校验。"""

    def _compile(self, wf):
        return compile_ast(parse_workflow(wf), workflow_id="wf-1", version="1").to_dict()

    def test_upsert_hits_existing_patches(self):
        ir = self._compile(mini_webhook_workflow())
        calls = []
        def fake_urlopen(request, timeout=30.0):
            if request.get_method() == "GET":
                calls.append(("GET", request.full_url))
                return _FakeResponse(json.dumps({"data": [{"id": "wf-old"}], "nextCursor": None}).encode())
            calls.append((request.get_method(), request.full_url, json.loads(request.data)))
            return _FakeResponse(json.dumps({"id": "wf-old", "name": "mini"}).encode())
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            created = deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k",
                                    mode="upsert")
        self.assertEqual(created["id"], "wf-old")
        self.assertEqual(calls[0][0], "GET")
        self.assertIn("name=wf-1", calls[0][1])  # name 缺省取 IR workflow.id
        self.assertEqual(calls[1][0], "PUT")
        self.assertTrue(calls[1][1].endswith("/api/v1/workflows/wf-old"))
        self.assertNotIn("id", calls[1][2])  # PATCH body 同样剥 id

    def test_upsert_miss_creates(self):
        ir = self._compile(mini_webhook_workflow())
        calls = []
        def fake_urlopen(request, timeout=30.0):
            if request.get_method() == "GET":
                calls.append(("GET", request.full_url))
                return _FakeResponse(b'{"data": [], "nextCursor": null}')
            calls.append((request.get_method(), request.full_url))
            return _FakeResponse(b'{"id":"wf-new","name":"mini"}')
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            created = deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k",
                                    mode="upsert")
        self.assertEqual(created["id"], "wf-new")
        self.assertEqual(calls[1][0], "POST")

    def test_unknown_mode_rejected(self):
        ir = self._compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock, \
                self.assertRaises(ValueError):
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k",
                          mode="bogus")
        urlopen_mock.assert_not_called()

    def test_response_without_id_rejected(self):
        # P3-3：2xx 但响应非 workflow（无 id）显式失败
        ir = self._compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(b'{"message":"ok"}')
            with self.assertRaises(ValueError) as ctx:
                deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k")
        self.assertIn("missing workflow id", str(ctx.exception))


def _cred_workflow() -> dict:
    """带凭据引用的最小工作流（httpHeaderAuth 凭据，源实例 id=src-1）。"""
    return {
        "nodes": [
            {"name": "Trigger", "type": "n8n-nodes-base.manualTrigger",
             "typeVersion": 1, "position": [0, 0], "parameters": {}},
            {"name": "Req", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4,
             "position": [0, 1], "parameters": {"url": "https://example.com"},
             "credentials": {"httpHeaderAuth": {"id": "src-1", "name": "MyHeaderAuth"}}},
        ],
        "connections": {"Trigger": {"main": [[{"node": "Req"}]]}},
    }


class TestCredentialsResolution(unittest.TestCase):
    """P2-3：凭据 name->id 部署前映射（跨实例）。"""

    def _compile(self, wf):
        return compile_ast(parse_workflow(wf), workflow_id="wf-cred", version="1").to_dict()

    def test_no_credentials_zero_lookup(self):
        # 无凭据引用的工作流不触发 GET /credentials（矩阵场景零额外开销）
        ir = self._compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(b'{"id":"wf-1"}')
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k")
        self.assertEqual(urlopen_mock.call_count, 1)  # 仅 POST

    def test_credentials_resolved_by_name(self):
        ir = self._compile(_cred_workflow())
        calls = []
        def fake_urlopen(request, timeout=30.0):
            if "credentials" in request.full_url:
                calls.append(("GET", request.full_url))
                return _FakeResponse(b'{"data":[{"id":"tgt-1","name":"MyHeaderAuth","type":"httpHeaderAuth"}],"nextCursor":null}')
            calls.append(("POST", request.full_url, json.loads(request.data)))
            return _FakeResponse(b'{"id":"wf-new","name":"wf-cred"}')
        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k")
        self.assertEqual(calls[0][0], "GET")
        body = calls[1][2]
        req = next(n for n in body["nodes"] if n["name"] == "Req")
        self.assertEqual(req["credentials"]["httpHeaderAuth"],
                         {"id": "tgt-1", "name": "MyHeaderAuth"})  # 源 id 被替换

    def test_missing_credential_fails_explicitly(self):
        ir = self._compile(_cred_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(b'{"data":[],"nextCursor":null}')
            with self.assertRaises(ValueError) as ctx:
                deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k")
        self.assertIn("MyHeaderAuth", str(ctx.exception))
        self.assertIn("missing", str(ctx.exception))

    def test_credential_map_skips_lookup(self):
        ir = self._compile(_cred_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(b'{"id":"wf-new"}')
            deploy_to_n8n(ir, base_url="https://n8n.example.com", api_key="k",
                          credential_map={"MyHeaderAuth": "tgt-9"})
        self.assertEqual(urlopen_mock.call_count, 1)  # 无 GET，仅 POST
        body = _capture(urlopen_mock)["body"]
        req = next(n for n in body["nodes"] if n["name"] == "Req")
        self.assertEqual(req["credentials"]["httpHeaderAuth"]["id"], "tgt-9")


class TestDeployIrJson(unittest.TestCase):
    def test_json_entry_deploys(self):
        ir = _compile(mini_webhook_workflow())
        with mock.patch("urllib.request.urlopen") as urlopen_mock:
            urlopen_mock.return_value = _FakeResponse(b'{"id":"wf-1"}')
            deploy_ir_json(json.dumps(ir), base_url="https://n8n.example.com", api_key="k")
        captured = _capture(urlopen_mock)
        self.assertEqual(captured["url"], "https://n8n.example.com/api/v1/workflows")

    def test_invalid_json_rejected(self):
        with self.assertRaises(ValueError):
            deploy_ir_json("{nope", base_url="https://n8n.example.com", api_key="k")

    def test_non_dict_rejected(self):
        with self.assertRaises(ValueError):
            deploy_ir_json('"nope"', base_url="https://n8n.example.com", api_key="k")


if __name__ == "__main__":
    unittest.main()
