"""The MCP endpoint — Streamable HTTP, stateless, JSON responses
(spec 2025-11-25, also answering 2025-06-18 / 2025-03-26 clients).

- ``POST`` one JSON-RPC message (or, for 2025-03-26 clients, a batch):
  requests get ``application/json``; notifications and responses get
  ``202 Accepted``. No session id is issued — every request stands alone.
- ``GET`` / ``DELETE`` → 405: no server-initiated stream, no sessions.
- Every request needs an OAuth access token issued for this endpoint
  (``Authorization: Bearer``); without one: ``401`` with
  ``WWW-Authenticate: Bearer resource_metadata=…`` so the client can discover
  the authorization server and sign the user in (RFC 9728).
- A present ``Origin`` header must be ours or a known assistant's (DNS
  rebinding protection) → otherwise 403.
- An unsupported ``MCP-Protocol-Version`` header → 400.

Cookies are never read here: a browser session can't drive the tools, and no
CSRF token is needed.
"""

import json
import logging

import posthog
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import translation
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from oauth2_provider.oauth2_backends import get_oauthlib_core

from the_cauldron.mcp_server import tools  # noqa: F401  (registers every tool)
from the_cauldron.mcp_server.context import McpContext
from the_cauldron.mcp_server.errors import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    InsufficientScope,
    ProtocolError,
)
from the_cauldron.mcp_server.instructions import server_instructions
from the_cauldron.mcp_server.registry import READ, WRITE, call_tool, list_tools
from the_cauldron.oauth_views import mcp_resource_url, touch_connection
from the_cauldron.services import nutrition

logger = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
LATEST_VERSION = SUPPORTED_VERSIONS[0]
SERVER_VERSION = "1.0.0"
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
        response = HttpResponse(status=405)
        response["Allow"] = "POST"
        return response

    delete = get

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
        version = request.headers.get("MCP-Protocol-Version")
        if version and version not in SUPPORTED_VERSIONS:
            return JsonResponse(
                _rpc_error(None, INVALID_REQUEST, f"Unsupported MCP-Protocol-Version: {version}"), status=400
            )
        token, error = self._authenticate(request)
        if error:
            return error
        try:
            message = json.loads(request.body or b"")
        except (ValueError, UnicodeDecodeError):
            return JsonResponse(_rpc_error(None, PARSE_ERROR, "Parse error"), status=400)

        # The token's user, for the web serializers the write tools reuse
        # (they read request.user). Only this view sees it: no session is made.
        request.user = token.user
        conn = touch_connection(token.user, token.application)
        _identify_for_analytics(token.user)
        ctx = McpContext(
            user=token.user,
            application=token.application,
            scopes=frozenset((token.scope or "").split()),
            connection=conn,
            request=request,
            language="es" if (conn.language or "").startswith("es") else "en",
            tz=nutrition.user_tz(token.user),
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
        return JsonResponse(reply)

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
            return {"jsonrpc": "2.0", "id": id_, "result": self._result(ctx, method, params)}
        except ProtocolError as exc:
            return _rpc_error(id_, exc.code, exc.message, exc.data)
        except InsufficientScope:
            raise
        except Exception:
            logger.exception("MCP method %s failed", method)
            return _rpc_error(id_, INTERNAL_ERROR, "Internal error")

    def _result(self, ctx, method, params):
        if method == "initialize":
            requested = params.get("protocolVersion")
            with translation.override(ctx.language):
                return {
                    "protocolVersion": requested if requested in SUPPORTED_VERSIONS else LATEST_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "the-cauldron",
                        "title": "The Cauldron",
                        "version": SERVER_VERSION,
                        "websiteUrl": ctx.absolute(reverse("the_cauldron:landing")),
                    },
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
