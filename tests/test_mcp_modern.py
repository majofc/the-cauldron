"""MCP spec 2026-07-28 ("modern": stateless, per-request _meta) next to the
legacy initialize-based versions (#89 hotfix).

Claude speaks 2026-07-28 first. Before this, its requests got a generic 400
for the unknown ``MCP-Protocol-Version`` (before authentication), so it never
loaded a tool.
"""

import base64
import json

import pytest

from the_cauldron.mcp_server.errors import HEADER_MISMATCH, METHOD_NOT_FOUND, UNSUPPORTED_PROTOCOL_VERSION
from the_cauldron.tests.test_mcp_server import _post, _token, _user

V = "2026-07-28"


@pytest.fixture
def alice(db):
    return _user("alice")


def _meta(version=V):
    return {"io.modelcontextprotocol/protocolVersion": version,
            "io.modelcontextprotocol/clientInfo": {"name": "claude-ai", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {}}


def _modern(token, method, params=None, *, headers=None, meta_version=V, id_=1):
    params = {**(params or {}), "_meta": _meta(meta_version)}
    h = {"HTTP_MCP_METHOD": method}
    if method == "tools/call":
        h["HTTP_MCP_NAME"] = params["name"]
    h.update(headers or {})
    return _post({"jsonrpc": "2.0", "id": id_, "method": method, "params": params}, token, version=V, **h)


def test_unknown_version_is_a_modern_error_listing_ours(alice):
    r = _post({"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}}, _token(alice), version="2099-01-01")
    assert r.status_code == 400
    err = r.json()["error"]
    assert r.json()["id"] == 7
    assert err["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert err["data"]["requested"] == "2099-01-01"
    assert V in err["data"]["supported"] and "2025-11-25" in err["data"]["supported"]


def test_discover(alice):
    r = _modern(_token(alice), "server/discover")
    assert r.status_code == 200, r.content
    res = r.json()["result"]
    assert res["resultType"] == "complete"
    assert res["supportedVersions"][0] == V and "2025-11-25" in res["supportedVersions"]
    assert res["capabilities"] == {"tools": {}}
    assert res["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "the-cauldron"
    assert res["instructions"] and res["cacheScope"] == "private" and res["ttlMs"] > 0


def test_tools_list_without_handshake(alice):
    res = _modern(_token(alice), "tools/list").json()["result"]
    assert res["resultType"] == "complete"
    assert len(res["tools"]) == 38
    assert res["cacheScope"] == "private" and res["ttlMs"] > 0
    assert "io.modelcontextprotocol/serverInfo" in res["_meta"]


def test_tools_call(alice):
    res = _modern(_token(alice), "tools/call", {"name": "get_goals", "arguments": {}}).json()["result"]
    assert res["resultType"] == "complete" and res["isError"] is False
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]


def test_base64_mcp_name_is_decoded(alice):
    encoded = "=?base64?" + base64.b64encode(b"get_goals").decode() + "?="
    r = _modern(_token(alice), "tools/call", {"name": "get_goals", "arguments": {}}, headers={"HTTP_MCP_NAME": encoded})
    assert r.status_code == 200 and r.json()["result"]["isError"] is False


@pytest.mark.parametrize("headers, meta_version", [
    ({"HTTP_MCP_METHOD": "tools/call"}, V),          # Mcp-Method doesn't match the body
    ({"HTTP_MCP_NAME": "delete_meal"}, V),           # Mcp-Name doesn't match params.name
    ({}, "2025-11-25"),                              # header version != _meta version
])
def test_header_mismatch(alice, headers, meta_version):
    method = "tools/call" if "HTTP_MCP_NAME" in headers else "tools/list"
    params = {"name": "get_goals", "arguments": {}} if method == "tools/call" else None
    r = _modern(_token(alice), method, params, headers=headers, meta_version=meta_version)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == HEADER_MISMATCH


def test_missing_mcp_method_header(alice):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {"_meta": _meta()}}
    r = _post(body, _token(alice), version=V)
    assert r.status_code == 400 and r.json()["error"]["code"] == HEADER_MISMATCH


def test_legacy_methods_are_unknown_in_modern_era(alice):
    for method in ("initialize", "ping"):
        r = _modern(_token(alice), method)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == METHOD_NOT_FOUND


def test_still_needs_a_token(db):
    r = _modern(None, "tools/list")
    assert r.status_code == 401 and "resource_metadata=" in r["WWW-Authenticate"]


def test_legacy_clients_unchanged(alice):
    token = _token(alice)
    init = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                 token, version=None).json()["result"]
    assert init["protocolVersion"] == "2025-11-25" and "resultType" not in init
    res = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, token).json()["result"]
    assert len(res["tools"]) == 38 and "resultType" not in res and "_meta" not in res
