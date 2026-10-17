"""Importing this package registers every tool with ``registry.TOOLS``."""

from the_cauldron.mcp_server.tools import (  # noqa: F401
    forge,
    forge_writes,
    grimoire,
    grimoire_writes,
    nutrition,
    nutrition_writes,
)
