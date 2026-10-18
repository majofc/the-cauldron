"""Errors of the MCP layer.

- ``ToolError`` — a tool ran but can't do what was asked. Returned to the
  model as a tool result with ``isError: true`` and a JSON body
  ``{"code", "message", …}`` it can act on (never a bare 400).
- ``ProtocolError`` — the JSON-RPC request itself is wrong (unknown method or
  tool, bad params): a JSON-RPC ``error`` object.
- ``InsufficientScope`` — the token lacks the scope a tool needs: HTTP 403 with
  a ``WWW-Authenticate`` scope challenge so the client can ask for it.
"""

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
# MCP-reserved codes (spec 2026-07-28).
HEADER_MISMATCH = -32020
UNSUPPORTED_PROTOCOL_VERSION = -32022


class ToolError(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code = code
        self.message = str(message)
        self.extra = extra

    def payload(self):
        return {"code": self.code, "message": self.message, **self.extra}


class ProtocolError(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class InsufficientScope(Exception):
    def __init__(self, scope):
        super().__init__(scope)
        self.scope = scope
