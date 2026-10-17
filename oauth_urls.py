"""OAuth routes for The Cauldron's MCP server, included once at the site root
by arcano (``path("", include("the_cauldron.oauth_urls"))``).

They sit in django-oauth-toolkit's own ``oauth2_provider`` namespace because
DOT reverses its endpoint names (``oauth2_provider:token`` …) to build the
discovery metadata. Only what an MCP client needs is exposed — DOT's
application-management pages are left out on purpose.

- ``/.well-known/oauth-authorization-server`` — RFC 8414 (issuer = site root)
- ``/.well-known/oauth-protected-resource[/cauldron/mcp/]`` — RFC 9728
- ``/cauldron/oauth/authorize/`` — consent screen (Cauldron login + legal gate)
- ``/cauldron/oauth/token/``, ``revoke_token/`` — token endpoint, revocation
- ``/cauldron/oauth/register/`` — Dynamic Client Registration (RFC 7591/7592)
"""

from django.urls import path
from oauth2_provider import views as dot
from oauth2_provider.views.metadata import OAuthServerMetadataView

from the_cauldron.oauth_views import (
    CauldronAuthorizationView,
    CauldronResourceMetadataView,
    GuardedRegistrationManagementView,
    GuardedRegistrationView,
)

app_name = "oauth2_provider"

urlpatterns = [
    path(".well-known/oauth-authorization-server", OAuthServerMetadataView.as_view(), name="oauth-server-metadata"),
    path(
        ".well-known/oauth-protected-resource/cauldron/mcp/",
        CauldronResourceMetadataView.as_view(),
        name="oauth-resource-metadata-path",
    ),
    path(".well-known/oauth-protected-resource", CauldronResourceMetadataView.as_view(), name="oauth-resource-metadata"),
    path("cauldron/oauth/authorize/", CauldronAuthorizationView.as_view(), name="authorize"),
    path("cauldron/oauth/token/", dot.TokenView.as_view(), name="token"),
    path("cauldron/oauth/revoke_token/", dot.RevokeTokenView.as_view(), name="revoke-token"),
    path("cauldron/oauth/register/", GuardedRegistrationView.as_view(), name="dcr-register"),
    # RFC 7592: a client reads/updates its own registration with the
    # registration token it got back — DOT's DCR response links here.
    path(
        "cauldron/oauth/register/<str:client_id>/",
        GuardedRegistrationManagementView.as_view(),
        name="dcr-register-management",
    ),
]
