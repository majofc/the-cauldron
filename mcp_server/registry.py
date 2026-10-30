"""Tool registry and the single dispatch point (``call_tool``).

Every ``tools/call`` goes through ``call_tool``, in this order: known tool →
token scope → input schema → consent (nutrition tools) → run in the user's
language and timezone → JSON result or an actionable error → one PostHog
event. A DRF-style throttle (scope ``cauldron_mcp``) would slot in here
without touching any tool.

A tool is a function ``handler(ctx, **arguments) -> dict`` registered with
``@tool(...)``. Read tools need ``cauldron:read``; write tools
``cauldron:write`` (which also grants reads).

Write tools also take an optional ``client_request_id``: a repeat within
``McpIdempotency.WINDOW_SECONDS`` returns the first result instead of writing
twice. **Two-step tools** (``confirm=True``: every edit and delete) never
apply on the first call: the handler runs with ``apply=False`` and returns
``{"target", "preview"}``; the caller gets that preview and a single-use
``confirm_token`` bound to user + tool + target + arguments, valid
``McpConfirmation.TTL_SECONDS``. Only a second call with the same arguments and
that token runs the handler with ``apply=True``. This holds even when the user
set "always allow" for the tool in their client.
"""

import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from datetime import timedelta

import posthog
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.http import Http404
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from jsonschema import Draft202012Validator
from rest_framework.exceptions import ValidationError as DRFValidationError

from the_cauldron.mcp_server.errors import INVALID_PARAMS, InsufficientScope, ProtocolError, ToolError
from the_cauldron.models import McpConfirmation, McpIdempotency
from the_cauldron.nutrition_writes import PlanConflict
from the_cauldron.services import age_gate, legal
from the_cauldron.services.grimoire import ConversionError
from the_cauldron.services.nutrition import NutritionInputError

logger = logging.getLogger(__name__)

READ = "cauldron:read"
WRITE = "cauldron:write"


@dataclass
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict
    handler: object
    scope: str = READ
    consent: bool = False
    confirm: bool = False
    annotations: dict = field(default_factory=dict)

    def definition(self):
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.input_schema,
            "annotations": {"title": self.title, **self.annotations},
        }


TOOLS = {}


CLIENT_REQUEST_ID = {
    "type": "string", "minLength": 1, "maxLength": 100,
    "description": "Optional: your own id for this request. Repeating it within 10 minutes returns the "
                   "first result instead of doing it twice — use it when retrying.",
}
CONFIRM_TOKEN = {
    "type": "string", "minLength": 10, "maxLength": 100,
    "description": "Leave out on the first call: nothing changes and you get a preview plus this token. "
                   "Show the preview to the user; only if they agree, call again with the same arguments "
                   "and the token. Single use, valid 5 minutes.",
}


def tool(name, *, title, description, schema=None, scope=READ, consent=False,
         read_only=None, destructive=False, idempotent=False, confirm=False):
    """Register a tool. ``schema`` is the JSON Schema of its arguments (an
    empty object by default); ``consent=True`` for anything touching the
    sensitive nutrition data (goals, meal log, plan); ``confirm=True`` makes
    it two-step (every edit and delete)."""
    schema = json.loads(json.dumps(schema or {"type": "object", "properties": {}, "additionalProperties": False}))
    if scope == WRITE:
        schema.setdefault("properties", {})["client_request_id"] = CLIENT_REQUEST_ID
    if confirm:
        schema["properties"]["confirm_token"] = CONFIRM_TOKEN
    Draft202012Validator.check_schema(schema)
    read_only = scope == READ if read_only is None else read_only
    annotations = {"readOnlyHint": read_only, "openWorldHint": False}
    if not read_only:
        annotations["destructiveHint"] = destructive
        annotations["idempotentHint"] = idempotent

    def register(fn):
        if name in TOOLS:
            raise ValueError(f"duplicate MCP tool {name}")
        TOOLS[name] = Tool(name, title, description, schema, fn, scope, consent, confirm, annotations)
        return fn

    return register


def list_tools():
    return [t.definition() for t in TOOLS.values()]


def has_scope(ctx, scope):
    return scope in ctx.scopes or (scope == READ and WRITE in ctx.scopes)


def age_error(ctx):
    """None when the user may use The Cauldron at all (#115); else why not."""
    code = age_gate.blocked(ctx.user)
    if code is None:
        return None
    if code == age_gate.AGE_REQUIRED:
        message = _("The Cauldron needs your birth year first. Open this page, confirm it, and ask again.")
    else:
        message = _("The Cauldron is only for people 18 and older.")
    return ToolError(code, message, accept_url=ctx.absolute(reverse("the_cauldron:age-confirm")))


def consent_error(ctx):
    """None when the user may use nutrition tools; else what they must do."""
    user = ctx.user
    if legal.pending_documents(user):
        url = ctx.absolute(reverse("the_cauldron:legal-accept"))
    elif not legal.has_sensitive_consent(user):
        url = ctx.absolute(reverse("the_cauldron:journal"))
    else:
        return None
    return ToolError(
        "consent_required",
        _("Goals, the meal log and the meal plan need your consent to handle sensitive data. "
          "Open this page, accept, and ask again."),
        accept_url=url,
    )


def _validation_message(error):
    where = "/".join(str(p) for p in error.absolute_path)
    return f"{where}: {error.message}" if where else error.message


def to_json(data):
    """JSON-native copy of a payload (dates, UUIDs, decimals as strings)."""
    return json.loads(json.dumps(data, cls=DjangoJSONEncoder))


def result(data, *, is_error=False):
    text = json.dumps(data, cls=DjangoJSONEncoder, ensure_ascii=False, separators=(",", ":"))
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": json.loads(text),
        "isError": is_error,
    }


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _payload_hash(arguments):
    return _digest(json.dumps(arguments, sort_keys=True, cls=DjangoJSONEncoder))


def _replayed(ctx, name, request_id):
    since = timezone.now() - timedelta(seconds=McpIdempotency.WINDOW_SECONDS)
    row = McpIdempotency.objects.filter(
        user=ctx.user, tool=name, client_request_id=request_id, created_at__gte=since
    ).first()
    return row.result if row else None


def _remember_result(ctx, name, request_id, data):
    # An old row with the same id is past its window: the id may be reused.
    McpIdempotency.objects.filter(user=ctx.user, tool=name, client_request_id=request_id).delete()
    try:
        with transaction.atomic():
            McpIdempotency.objects.create(user=ctx.user, tool=name, client_request_id=request_id, result=data)
    except IntegrityError:  # a concurrent retry stored it first
        pass


def _two_step(ctx, entry, arguments, token):
    """Preview (no token) or apply (valid token). Returns (data, confirm_step)."""
    prepared = entry.handler(ctx, apply=False, **arguments)
    target, payload = str(prepared["target"]), _payload_hash(arguments)
    if token is None:
        raw = secrets.token_urlsafe(32)
        McpConfirmation.objects.create(
            user=ctx.user, tool=entry.name, target=target, payload_hash=payload, token_hash=_digest(raw),
            expires_at=timezone.now() + timedelta(seconds=McpConfirmation.TTL_SECONDS),
        )
        return {
            "status": "confirmation_required",
            "preview": prepared["preview"],
            "confirm_token": raw,
            "expires_in_seconds": McpConfirmation.TTL_SECONDS,
            "message": _("Nothing has changed yet. Show this preview to the user; only if they agree, "
                         "call the tool again with the same arguments and confirm_token."),
        }, "preview"
    with transaction.atomic():
        claimed = McpConfirmation.objects.filter(
            user=ctx.user, tool=entry.name, target=target, payload_hash=payload,
            token_hash=_digest(token), used_at__isnull=True, expires_at__gt=timezone.now(),
        ).update(used_at=timezone.now())
        if not claimed:
            raise ToolError(
                "invalid_confirm_token",
                _("That confirmation is expired, already used, or was for different changes. "
                  "Call the tool again without confirm_token to get a new preview."),
            )
        # A failure here rolls the claim back, so the user can simply retry.
        return entry.handler(ctx, apply=True, **arguments), "applied"


def call_tool(ctx, name, arguments):
    """Run one tool for ``ctx``; returns an MCP ``CallToolResult`` dict."""
    entry = TOOLS.get(name)
    if entry is None:
        raise ProtocolError(INVALID_PARAMS, f"Unknown tool: {name}")
    if not has_scope(ctx, entry.scope):
        raise InsufficientScope(entry.scope)
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ProtocolError(INVALID_PARAMS, "arguments must be an object")

    started = time.monotonic()
    error_code = confirm_step = None
    try:
        errors = sorted(Draft202012Validator(entry.input_schema).iter_errors(arguments), key=str)
        if errors:
            raise ToolError("invalid_arguments", "; ".join(_validation_message(e) for e in errors[:5]))
        arguments = dict(arguments)
        request_id = arguments.pop("client_request_id", None)
        token = arguments.pop("confirm_token", None)
        with translation.override(ctx.language), timezone.override(ctx.tz):
            blocked = age_error(ctx)
            if blocked:
                raise blocked
            if entry.consent:
                blocked = consent_error(ctx)
                if blocked:
                    raise blocked
            if request_id:
                previous = _replayed(ctx, name, request_id)
                if previous is not None:
                    return result(previous)
            if entry.confirm:
                data, confirm_step = _two_step(ctx, entry, arguments, token)
            else:
                data = entry.handler(ctx, **arguments)
            data = to_json(data)
            if request_id and confirm_step != "preview":
                _remember_result(ctx, name, request_id, data)
        return result(data)
    except ToolError as exc:
        error_code = exc.code
        return result(exc.payload(), is_error=True)
    except (NutritionInputError, ConversionError) as exc:
        error_code = "invalid_input"
        return result({"code": error_code, "message": str(exc)}, is_error=True)
    except DRFValidationError as exc:
        error_code = "invalid_input"
        return result({"code": error_code, "message": _("Some values are not valid."),
                       "fields": to_json(exc.detail)}, is_error=True)
    except ValidationError as exc:
        error_code = "invalid_input"
        return result({"code": error_code, "message": "; ".join(exc.messages)}, is_error=True)
    except PlanConflict as exc:
        error_code = "conflict"
        return result({"code": error_code, "message": str(exc)}, is_error=True)
    except (ObjectDoesNotExist, Http404):
        error_code = "not_found"
        return result({"code": error_code, "message": _("Not found.")}, is_error=True)
    except Exception:  # never leak internals to the model; Sentry gets the trace
        logger.exception("MCP tool %s failed", name)
        error_code = "internal_error"
        return result({"code": error_code, "message": _("Something went wrong. Try again.")}, is_error=True)
    finally:
        posthog.capture("mcp_tool_called", properties={
            "tool": name,
            "ok": error_code is None,
            "error_code": error_code,
            "latency_ms": round((time.monotonic() - started) * 1000),
            "client": ctx.client,
            "confirm_step": confirm_step,
        })
