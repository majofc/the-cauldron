"""Claude connects through its Client ID Metadata Document (#89 hotfix).

Claude's document lists the jwt-bearer grant besides authorization_code and
refresh_token; DOT alone refuses that ("invalid_request" on the consent page).
``CauldronMetadataFetcher`` drops grant types we never issue.
"""

import base64
import hashlib
import secrets

import pytest
from django.test import Client
from oauth2_provider.cimd import SafeMetadataFetcher

from the_cauldron.oauth_cimd import CauldronMetadataFetcher
from the_cauldron.tests.test_mcp_server import _user

CLAUDE_ID = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CALLBACK = "https://claude.ai/api/mcp/auth_callback"
# As served by claude.ai on 2026-10-17.
CLAUDE_DOC = {
    "client_id": CLAUDE_ID,
    "client_name": "Claude",
    "client_uri": "https://claude.ai",
    "redirect_uris": [CALLBACK],
    "grant_types": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:jwt-bearer"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
}


@pytest.fixture
def claude_doc(monkeypatch):
    """No network: the base fetcher returns Claude's document."""
    monkeypatch.setattr(SafeMetadataFetcher, "fetch", lambda self, client_id: (dict(CLAUDE_DOC), 300))


def _authorize(client):
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(secrets.token_urlsafe(48).encode()).digest()
    ).rstrip(b"=").decode()
    return client.get("/cauldron/oauth/authorize/", {
        "response_type": "code", "client_id": CLAUDE_ID, "redirect_uri": CALLBACK,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
        "scope": "cauldron:read cauldron:write", "resource": "http://testserver/cauldron/mcp/",
    })


def test_fetcher_drops_unsupported_grant_types(claude_doc):
    metadata, max_age = CauldronMetadataFetcher().fetch(CLAUDE_ID)
    assert metadata["grant_types"] == ["authorization_code", "refresh_token"]
    assert metadata["redirect_uris"] == [CALLBACK] and max_age == 300


def test_settings_use_the_cauldron_fetcher():
    from oauth2_provider.settings import oauth2_settings

    assert oauth2_settings.CIMD_METADATA_FETCHER is CauldronMetadataFetcher


def test_claude_reaches_the_consent_form(db, claude_doc):
    user = _user("ana")
    c = Client()
    c.force_login(user)
    r = _authorize(c)
    assert r.status_code == 200
    assert not r.context.get("error")
    body = r.content.decode()
    assert "Claude" in body and 'name="allow"' in body
