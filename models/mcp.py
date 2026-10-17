"""The Cauldron's MCP server (#89): which AI assistants a user connected.

``McpConnection`` — one per (user, OAuth client). Created when the user
approves a client on the consent screen; tokens themselves live in
django-oauth-toolkit's tables. The profile page lists these, and
"Disconnect" deletes the row together with the client's tokens for that user.
"""

from django.conf import settings
from django.db import models
from oauth2_provider.settings import oauth2_settings

from .forge import ForgeBaseModel


class McpConnection(ForgeBaseModel):
    class Client(models.TextChoices):
        CLAUDE = "claude", "Claude"
        CLAUDE_CODE = "claude_code", "Claude Code"
        OTHER = "other", "Other"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mcp_connections")
    application = models.ForeignKey(
        oauth2_settings.APPLICATION_MODEL, on_delete=models.CASCADE, related_name="cauldron_connections"
    )
    # Normalized client kind — the only client detail analytics ever sees (an
    # enum, never the free-text name). "claude*" only for Anthropic's own
    # clients (``client_identity``); a self-declared name never earns it.
    client = models.CharField(max_length=12, choices=Client.choices, default=Client.OTHER)
    # The page language when the user connected: tool answers use it.
    language = models.CharField(max_length=8, default="es")
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "application"], name="uniq_mcp_connection"),
        ]

    def __str__(self):
        return f"{self.user} ↔ {self.application_id}"

    @staticmethod
    def client_for(application):
        from the_cauldron.client_identity import is_anthropic_client

        if not is_anthropic_client(application.client_id):
            return McpConnection.Client.OTHER
        hint = f"{application.client_id} {application.name}".lower()
        if "claude code" in hint or "claude-code" in hint:
            return McpConnection.Client.CLAUDE_CODE
        return McpConnection.Client.CLAUDE


class McpConfirmation(ForgeBaseModel):
    """A pending edit/delete awaiting the user's go-ahead (two-step tools).

    The first call changes nothing and returns a preview plus a random token;
    only its SHA-256 is stored. The second call must bring the token back for
    the same user, tool, target and payload within ``TTL`` — and only once.
    """

    TTL_SECONDS = 300

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    tool = models.CharField(max_length=64)
    target = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64)
    token_hash = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)


class McpIdempotency(ForgeBaseModel):
    """The result of a write tool called with a ``client_request_id``: a repeat
    within ``WINDOW`` returns this instead of writing twice."""

    WINDOW_SECONDS = 600

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    tool = models.CharField(max_length=64)
    client_request_id = models.CharField(max_length=100)
    result = models.JSONField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tool", "client_request_id"], name="uniq_mcp_request"),
        ]
