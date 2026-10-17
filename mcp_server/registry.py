"""Tool registry and the single dispatch point (``call_tool``).

Every ``tools/call`` goes through ``call_tool``, in this order: known tool →
token scope → input schema → consent (nutrition tools) → run in the user's
language and timezone → JSON result or an actionable error → one PostHog
event. A DRF-style throttle (scope ``cauldron_mcp``) would slot in here
without touching any tool.

A tool is a function ``handler(ctx, **arguments) -> dict`` registered with
``@tool(...)``. Read tools need ``cauldron:read``; write tools
``cauldron:write`` (which also grants reads).
"""

import json
import logging
import time
from dataclasses import dataclass, field

import posthog
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.http import Http404
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.translation import gettext as _
from jsonschema import Draft202012Validator

from the_cauldron.mcp_server.errors import INVALID_PARAMS, InsufficientScope, ProtocolError, ToolError
from the_cauldron.services import legal
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


def tool(name, *, title, description, schema=None, scope=READ, consent=False,
         read_only=None, destructive=False, idempotent=False):
    """Register a tool. ``schema`` is the JSON Schema of its arguments (an
    empty object by default); ``consent=True`` for anything touching the
    sensitive nutrition data (goals, meal log, plan)."""
    schema = schema or {"type": "object", "properties": {}, "additionalProperties": False}
    Draft202012Validator.check_schema(schema)
    read_only = scope == READ if read_only is None else read_only
    annotations = {"readOnlyHint": read_only, "openWorldHint": False}
    if not read_only:
        annotations["destructiveHint"] = destructive
        annotations["idempotentHint"] = idempotent

    def register(fn):
        if name in TOOLS:
            raise ValueError(f"duplicate MCP tool {name}")
        TOOLS[name] = Tool(name, title, description, schema, fn, scope, consent, annotations)
        return fn

    return register


def list_tools():
    return [t.definition() for t in TOOLS.values()]


def has_scope(ctx, scope):
    return scope in ctx.scopes or (scope == READ and WRITE in ctx.scopes)


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
    error_code = None
    try:
        errors = sorted(Draft202012Validator(entry.input_schema).iter_errors(arguments), key=str)
        if errors:
            raise ToolError("invalid_arguments", "; ".join(_validation_message(e) for e in errors[:5]))
        with translation.override(ctx.language), timezone.override(ctx.tz):
            if entry.consent:
                blocked = consent_error(ctx)
                if blocked:
                    raise blocked
            data = entry.handler(ctx, **arguments)
        return result(data)
    except ToolError as exc:
        error_code = exc.code
        return result(exc.payload(), is_error=True)
    except (NutritionInputError, ConversionError) as exc:
        error_code = "invalid_input"
        return result({"code": error_code, "message": str(exc)}, is_error=True)
    except ValidationError as exc:
        error_code = "invalid_input"
        return result({"code": error_code, "message": "; ".join(exc.messages)}, is_error=True)
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
        })
