"""The MCP endpoint — Streamable HTTP, stateless, JSON responses. Dual-era:

- **Modern** (spec 2026-07-28, ``MCP-Protocol-Version: 2026-07-28``): no
  handshake; every request carries its version in ``params._meta`` and the
  ``Mcp-Method`` (+ ``Mcp-Name`` for ``tools/call``) headers, which must match
  the body (else 400 ``HeaderMismatch``). ``server/discover`` advertises the
  versions; results carry ``resultType`` and ``_meta`` server info; an unknown
  method is 404.
- **Legacy** (2025-11-25 / 2025-06-18 / 2025-03-26, or no header):
  ``initialize`` handshake semantics, answered statelessly.
- An unknown version → 400 ``UnsupportedProtocolVersionError`` listing ours,
  so a modern client retries with one of them (or falls back to ``initialize``).

- ``POST`` one JSON-RPC message (or, for 2025-03-26 clients, a batch):
  requests get ``application/json``; notifications and responses get
  ``202 Accepted``. No session id is issued — every request stands alone.
- ``GET`` / ``DELETE`` → 405: no server-initiated stream, no sessions —
  except a browser asking for HTML, which gets the info page (``mcp_info_view``).
- Every request needs an OAuth access token issued for this endpoint
  (``Authorization: Bearer``); without one: ``401`` with
  ``WWW-Authenticate: Bearer resource_metadata=…`` so the client can discover
  the authorization server and sign the user in (RFC 9728).
- A present ``Origin`` header must be ours or a known assistant's (DNS
  rebinding protection) → otherwise 403.

Cookies are never read here: a browser session can't drive the tools, and no
CSRF token is needed.
"""

import base64
import json
import logging

import posthog
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import translation
from django.utils.cache import patch_vary_headers
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.oauth2_backends import get_oauthlib_core

from the_cauldron.mcp_server import tools  # noqa: F401  (registers every tool)
from the_cauldron.mcp_server.context import McpContext
from the_cauldron.mcp_server.errors import (
    HEADER_MISMATCH,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    UNSUPPORTED_PROTOCOL_VERSION,
    InsufficientScope,
    ProtocolError,
)
from the_cauldron.mcp_server.instructions import server_instructions
from the_cauldron.mcp_server.registry import READ, WRITE, call_tool, list_tools
from the_cauldron.oauth_views import mcp_info_view, mcp_resource_url, touch_connection
from the_cauldron.services import nutrition

logger = logging.getLogger(__name__)

MODERN_VERSIONS = ("2026-07-28",)
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SUPPORTED_VERSIONS = MODERN_VERSIONS + LEGACY_VERSIONS
LATEST_VERSION = LEGACY_VERSIONS[0]  # what a legacy ``initialize`` negotiates down to
SERVER_VERSION = "1.0.0"
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"
# tools/list and server/discover are cacheable (2026-07-28); "private" because
# the instructions follow the user's language.
LIST_TTL_MS = 5 * 60 * 1000
_UNPARSABLE = object()
DEFAULT_ALLOWED_ORIGINS = ("https://claude.ai", "https://claude.com")


def _allowed_origin(request, origin):
    own = f"{request.scheme}://{request.get_host()}"
    allowed = set(getattr(settings, "CAULDRON_MCP_ALLOWED_ORIGINS", DEFAULT_ALLOWED_ORIGINS)) | {own}
    return origin.rstrip("/") in {o.rstrip("/") for o in allowed}


def _rpc_error(id_, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id_, "error": err}


def _decode_header(value):
    """``=?base64?…?=`` sentinel values (2026-07-28 Value Encoding) → text."""
    if value and value.startswith("=?base64?") and value.endswith("?="):
        try:
            return base64.b64decode(value[9:-2], validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    return value


def _header_mismatch(request, message):
    """None if the mirrored headers match the body; else the reason (modern era)."""
    if not isinstance(message, dict) or "method" not in message:
        return None
    if "id" not in message:  # notifications: no header requirements in this revision
        return None
    # A header that is present must match the body (that is what protects an
    # intermediary routing on headers); one a client leaves out isn't held
    # against it — nothing in front of us routes on them.
    method = message["method"]
    header_method = request.headers.get("Mcp-Method")
    if header_method is not None and header_method != method:
        return f"Mcp-Method header must be '{method}'"
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    if META_VERSION in meta and meta[META_VERSION] != request.headers.get("MCP-Protocol-Version"):
        return f"MCP-Protocol-Version header must match params._meta['{META_VERSION}']"
    header_name = request.headers.get("Mcp-Name")
    if method == "tools/call" and header_name is not None and _decode_header(header_name) != params.get("name"):
        return "Mcp-Name header must match params.name"
    return None


def _log_request(request, response):
    """One ``McpRequestLog`` row (technical metadata only); never breaks a request."""
    try:
        from the_cauldron.models import McpRequestLog

        info = getattr(request, "_mcp_log", {})
        error_code = None
        if response.get("Content-Type", "").startswith("application/json") and response.content:
            body = json.loads(response.content)
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                error_code = body["error"].get("code")
                info.setdefault("detail", str(body["error"].get("message", "")))
            elif isinstance(body, dict) and isinstance(body.get("result"), dict) and body["result"].get("isError"):
                info.setdefault("detail", "tool error")
        McpRequestLog.objects.create(
            status=response.status_code,
            rpc_method=info.get("rpc_method", ""),
            error_code=error_code if isinstance(error_code, int) else None,
            detail=(info.get("detail") or "")[:200],
            protocol_version=(request.headers.get("MCP-Protocol-Version") or "")[:32],
            header_method=(request.headers.get("Mcp-Method") or "")[:64],
            has_bearer=request.headers.get("Authorization", "").startswith("Bearer "),
            authenticated=bool(info.get("authenticated")),
            client=info.get("client", ""),
            user_agent=(request.headers.get("User-Agent") or "")[:200],
        )
        stale = McpRequestLog.objects.order_by("-created_at").values_list("pk", flat=True)[McpRequestLog.KEEP:]
        McpRequestLog.objects.filter(pk__in=list(stale)).delete()
    except Exception:  # diagnostics must never break the endpoint
        logger.exception("MCP request log failed")


def _resource_metadata_url(request):
    return request.build_absolute_uri(reverse("oauth2_provider:oauth-resource-metadata-path"))


def _challenge(request, status, error=None, scope=None):
    parts = [f'resource_metadata="{_resource_metadata_url(request)}"', f'scope="{scope or f"{READ} {WRITE}"}"']
    if error:
        parts.insert(0, f'error="{error}"')
    response = JsonResponse(
        _rpc_error(None, INVALID_REQUEST, "Unauthorized" if status == 401 else "Insufficient scope"),
        status=status,
    )
    response["WWW-Authenticate"] = "Bearer " + ", ".join(parts)
    return response


def _identify_for_analytics(user):
    """Tie this request's events to the internal user id, honouring opt-outs
    exactly like ``arcano.middleware`` does for cookie-authenticated pages."""
    if posthog.disabled:
        return
    posthog.identify_context(str(user.pk))
    try:
        from arcano.analytics import is_opted_out, opt_out_current_context
    except ImportError:  # the_cauldron running without the arcano project
        return
    if is_opted_out(user):
        opt_out_current_context()


@method_decorator(csrf_exempt, name="dispatch")
class McpView(View):
    http_method_names = ["get", "post", "delete", "options"]

    def dispatch(self, request, *args, **kwargs):
        origin = request.headers.get("Origin")
        if origin and not _allowed_origin(request, origin):
            return JsonResponse(_rpc_error(None, INVALID_REQUEST, "Origin not allowed"), status=403)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        accept = request.headers.get("Accept", "")
        if "text/html" in accept and "text/event-stream" not in accept:
            response = mcp_info_view(request)
        else:
            response = self.delete(request)
        patch_vary_headers(response, ["Accept"])
        return response

    def delete(self, request, *args, **kwargs):
        response = HttpResponse(status=405)
        response["Allow"] = "POST"
        return response

    def _authenticate(self, request):
        """``(token, None)`` or ``(None, error_response)``."""
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            return None, _challenge(request, 401)
        valid, oauth_request = get_oauthlib_core().verify_request(request, scopes=[])
        token = getattr(oauth_request, "access_token", None)
        if not valid or token is None or token.user is None or not token.user.is_active:
            return None, _challenge(request, 401, error="invalid_token")
        # RFC 8707: a token bound to another resource isn't for us.
        own = mcp_resource_url(request).rstrip("/")
        if token.resource and own not in {str(r).rstrip("/") for r in token.resource}:
            return None, _challenge(request, 401, error="invalid_token")
        return token, None

    def post(self, request, *args, **kwargs):
        request._mcp_log = {}
        response = self._post(request)
        _log_request(request, response)
        return response

    def _post(self, request):
        version = request.headers.get("MCP-Protocol-Version")
        try:
            message = json.loads(request.body or b"")
        except (ValueError, UnicodeDecodeError):
            message = _UNPARSABLE
        id_ = message.get("id") if isinstance(message, dict) else None
        if isinstance(message, dict):
            request._mcp_log["rpc_method"] = str(message.get("method", ""))[:64]
        elif isinstance(message, list):
            request._mcp_log["rpc_method"] = "(batch)"
        if version and version not in SUPPORTED_VERSIONS:
            return JsonResponse(_rpc_error(id_, UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version", {
                "supported": list(SUPPORTED_VERSIONS), "requested": version,
            }), status=400)
        modern = version in MODERN_VERSIONS
        token, error = self._authenticate(request)
        if error:
            return error
        if message is _UNPARSABLE:
            return JsonResponse(_rpc_error(None, PARSE_ERROR, "Parse error"), status=400)
        if modern:
            if isinstance(message, list):
                return JsonResponse(_rpc_error(None, INVALID_REQUEST, "Batches are not supported"), status=400)
            mismatch = _header_mismatch(request, message)
            request._mcp_log["detail"] = mismatch or ""
            if mismatch:
                return JsonResponse(_rpc_error(id_, HEADER_MISMATCH, f"Header mismatch: {mismatch}"), status=400)

        # The token's user, for the web serializers the write tools reuse
        # (they read request.user). Only this view sees it: no session is made.
        request.user = token.user
        conn = touch_connection(token.user, token.application)
        request._mcp_log.update(authenticated=True, client=conn.client)
        _identify_for_analytics(token.user)
        ctx = McpContext(
            user=token.user,
            application=token.application,
            scopes=frozenset((token.scope or "").split()),
            connection=conn,
            request=request,
            language="es" if (conn.language or "").startswith("es") else "en",
            tz=nutrition.user_tz(token.user),
            modern=modern,
        )
        try:
            if isinstance(message, list):  # JSON-RPC batch (2025-03-26 clients only)
                if not message:
                    return JsonResponse(_rpc_error(None, INVALID_REQUEST, "Empty batch"), status=400)
                replies = [r for r in (self._handle(ctx, m) for m in message) if r is not None]
                return JsonResponse(replies, safe=False) if replies else HttpResponse(status=202)
            reply = self._handle(ctx, message)
        except InsufficientScope as exc:
            return _challenge(request, 403, error="insufficient_scope", scope=f"{READ} {exc.scope}")
        if reply is None:
            return HttpResponse(status=202)
        not_found = modern and reply.get("error", {}).get("code") == METHOD_NOT_FOUND
        return JsonResponse(reply, status=404 if not_found else 200)

    def _handle(self, ctx, message):
        """One JSON-RPC message → a reply dict, or None for notifications/responses."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _rpc_error(None, INVALID_REQUEST, "Invalid Request")
        if "method" not in message:  # a response to something we never asked
            return None
        method = message["method"]
        if "id" not in message:  # notification (initialized, cancelled, …)
            return None
        id_ = message["id"]
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return _rpc_error(id_, INVALID_REQUEST, "params must be an object")
        try:
            result = self._result(ctx, method, params)
            if ctx.modern:
                result = {"resultType": "complete", **result}
                result["_meta"] = {**result.get("_meta", {}), META_SERVER_INFO: self._server_info(ctx)}
            return {"jsonrpc": "2.0", "id": id_, "result": result}
        except ProtocolError as exc:
            return _rpc_error(id_, exc.code, exc.message, exc.data)
        except InsufficientScope:
            raise
        except Exception:
            logger.exception("MCP method %s failed", method)
            return _rpc_error(id_, INTERNAL_ERROR, "Internal error")

    def _server_info(self, ctx):
        return {
            "name": "the-cauldron",
            "title": "The Cauldron",
            "version": SERVER_VERSION,
            "websiteUrl": ctx.absolute(reverse("the_cauldron:landing")),
        }

    def _result(self, ctx, method, params):
        if ctx.modern:
            if method == "server/discover":
                with translation.override(ctx.language):
                    return {
                        "supportedVersions": list(SUPPORTED_VERSIONS),
                        "capabilities": {"tools": {}},
                        "instructions": server_instructions(ctx),
                        "ttlMs": LIST_TTL_MS,
                        "cacheScope": "private",
                    }
            if method == "tools/list":
                return {"tools": list_tools(), "ttlMs": LIST_TTL_MS, "cacheScope": "private"}
        else:
            if method == "initialize":
                requested = params.get("protocolVersion")
                with translation.override(ctx.language):
                    return {
                        "protocolVersion": requested if requested in LEGACY_VERSIONS else LATEST_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": self._server_info(ctx),
                        "instructions": server_instructions(ctx),
                    }
            if method == "ping":
                return {}
            if method == "tools/list":
                return {"tools": list_tools()}
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise ProtocolError(INVALID_REQUEST, "params.name is required")
            return call_tool(ctx, name, params.get("arguments"))
        raise ProtocolError(METHOD_NOT_FOUND, f"Method not found: {method}")
