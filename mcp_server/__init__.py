"""The Cauldron's MCP server (#89) — lets a user drive The Cauldron from their
own AI assistant (claude.ai, Claude mobile/Desktop, Claude Code).

- ``protocol`` — the Streamable HTTP endpoint (``/cauldron/mcp/``): stateless
  JSON-RPC with JSON responses, OAuth bearer tokens, origin + version checks.
- ``registry`` — tool definitions and the single dispatch point every call
  goes through (scope, input schema, consent, language/timezone, errors,
  analytics), so a throttle can be added later in one place.
- ``tools`` — the tools themselves: thin adapters over ``services/`` and
  ``nutrition_payloads`` — no business logic of their own.

Served by a plain Django view (no ASGI, no MCP SDK): see README "MCP".
"""
