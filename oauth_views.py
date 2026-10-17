"""OAuth 2.1 for The Cauldron's MCP server (#89).

django-oauth-toolkit does the protocol work (authorization code + S256 PKCE,
token rotation, Dynamic Client Registration, Client ID Metadata Documents,
RFC 8414 metadata); these views add what's ours:

- ``CauldronAuthorizationView`` — the consent screen in Cauldron styling,
  behind the Cauldron login and the legal gate. It names the place the code
  goes back to (the redirect host) and warns when that's only this computer,
  as the MCP authorization spec asks. Approving records an ``McpConnection``.
- ``CauldronResourceMetadataView`` — RFC 9728 protected resource metadata whose
  ``resource`` is exactly the MCP endpoint URL users paste into their assistant.
- ``McpConnectionsView`` / ``McpConnectionDetailView`` — the profile's list of
  connected assistants and "Disconnect", which revokes that client's tokens for
  the user at once (their next call gets a 401).
"""

from urllib.parse import urlsplit

import posthog
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.translation import get_language
from oauth2_provider.models import get_access_token_model, get_grant_model, get_refresh_token_model
from oauth2_provider.views import AuthorizationView
from oauth2_provider.views.metadata import OAuthProtectedResourceMetadataView
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from the_cauldron.models import McpConnection
from the_cauldron.views import cauldron_page

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def mcp_resource_url(request):
    """The MCP endpoint as users paste it — the token audience (RFC 8707)."""
    return request.build_absolute_uri(reverse("the_cauldron:mcp"))


@method_decorator(cauldron_page, name="dispatch")
class CauldronAuthorizationView(AuthorizationView):
    template_name = "the_cauldron/oauth_authorize.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        redirect_uri = ctx.get("redirect_uri") or (self.oauth2_data or {}).get("redirect_uri") or ""
        host = urlsplit(redirect_uri).hostname or ""
        ctx["redirect_host"] = host
        ctx["redirect_is_loopback"] = host in LOOPBACK_HOSTS
        ctx["can_write"] = "cauldron:write" in (ctx.get("scopes") or [])
        return ctx

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.cleaned_data.get("allow") and response.status_code in (301, 302):
            self._remember(form.cleaned_data["client_id"])
        return response

    def _remember(self, client_id):
        from oauth2_provider.models import get_application_model

        app = get_application_model().objects.filter(client_id=client_id).first()
        if app is None:
            return
        client = McpConnection.client_for(app.name)
        conn, created = McpConnection.objects.update_or_create(
            user=self.request.user, application=app,
            defaults={"client": client, "language": (get_language() or "es")[:8]},
        )
        if created:
            posthog.capture("mcp_connected", properties={"client": client})


class CauldronResourceMetadataView(OAuthProtectedResourceMetadataView):
    def get_resource(self, request):
        return mcp_resource_url(request)


class McpConnectionSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source="application.name", read_only=True)

    class Meta:
        model = McpConnection
        fields = ["uuid", "name", "client", "created_at", "last_used_at"]
        read_only_fields = fields


class McpConnectionsView(APIView):
    """``GET /cauldron/api/mcp/connections/`` — the user's connected assistants."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = McpConnection.objects.filter(user=request.user).select_related("application")
        return Response({"connections": McpConnectionSerializer(rows, many=True).data})


def revoke_connection(conn):
    """Delete the connection and every token/grant its client holds for the user."""
    with transaction.atomic():
        for model in (get_refresh_token_model(), get_access_token_model(), get_grant_model()):
            model.objects.filter(user=conn.user, application=conn.application).delete()
        conn.delete()


class McpConnectionDetailView(APIView):
    """``DELETE /cauldron/api/mcp/connections/{uuid}/`` — disconnect: the
    client's tokens for this user stop working immediately."""

    permission_classes = [IsAuthenticated]

    def delete(self, request, uuid):
        conn = get_object_or_404(McpConnection, uuid=uuid, user=request.user)
        client = conn.client
        revoke_connection(conn)
        posthog.capture("mcp_disconnected", properties={"client": client})
        return Response(status=status.HTTP_204_NO_CONTENT)


def touch_connection(user, application, *, every_seconds=300):
    """Record "last used" — at most every few minutes, not on every call."""
    now = timezone.now()
    conn = McpConnection.objects.filter(user=user, application=application).first()
    if conn is None:
        # Approved before this table existed, or a client authorized elsewhere:
        # still a connection the user should see and be able to disconnect.
        conn = McpConnection.objects.create(
            user=user, application=application, client=McpConnection.client_for(application.name),
            language=(get_language() or "es")[:8], last_used_at=now,
        )
    elif conn.last_used_at is None or (now - conn.last_used_at).total_seconds() > every_seconds:
        McpConnection.objects.filter(pk=conn.pk).update(last_used_at=now)
    return conn
