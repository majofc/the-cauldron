"""The Cauldron's MCP server (#89), phase 4: the info page, its analytics,
the nav links and the legal update.

A browser opening the connector URL gets the info page; an MCP client asking
for a stream still gets 405. Page and connect-click events carry only a flag
or an enum — never anything about the user.
"""

import pytest
from django.test import Client
from django.utils import translation

from the_cauldron.models import McpConnection
from the_cauldron.services import legal
from the_cauldron.tests.test_mcp_server import _app, _user

MCP = "/cauldron/mcp/"
HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


@pytest.fixture
def events(monkeypatch):
    rows = []
    monkeypatch.setattr("posthog.capture", lambda event, **kw: rows.append((event, kw.get("properties", {}))))
    return rows


@pytest.fixture
def alice(db):
    return _user("alice")


class TestInfoPage:
    def test_browser_gets_the_page_with_the_connector_url(self, db, events):
        r = Client().get(MCP, HTTP_ACCEPT=HTML)
        assert r.status_code == 200
        body = r.content.decode()
        assert "http://testserver/cauldron/mcp/" in body
        assert "claude mcp add --transport http cauldron http://testserver/cauldron/mcp/" in body
        assert "Accept" in r["Vary"]
        assert events == [("mcp_page_viewed", {"signed_in": False})]

    @pytest.mark.parametrize("accept", ["text/event-stream", "application/json", "", "text/html, text/event-stream"])
    def test_mcp_clients_still_get_405(self, db, accept):
        r = Client().get(MCP, HTTP_ACCEPT=accept)
        assert r.status_code == 405
        assert r["Allow"] == "POST"

    def test_spanish(self, db):
        with translation.override("es"):
            r = Client(HTTP_ACCEPT_LANGUAGE="es").get(MCP, HTTP_ACCEPT=HTML)
        assert "Tu Cauldron, desde tu asistente de IA" in r.content.decode()

    def test_signed_in_user_sees_their_connections(self, alice, events):
        McpConnection.objects.create(user=alice, application=_app(), language="en")
        c = Client()
        c.force_login(alice)
        body = c.get(MCP, HTTP_ACCEPT=HTML).content.decode()
        assert "You have 1 assistant connected." in body
        assert events == [("mcp_page_viewed", {"signed_in": True})]

    def test_linked_from_landing_footer_and_app_pages(self, alice):
        assert f'href="{MCP}"' in Client().get("/cauldron/").content.decode()
        c = Client()
        c.force_login(alice)
        for page in ("/cauldron/journal/", "/cauldron/grimoire/", "/cauldron/profile/"):
            assert f'href="{MCP}"' in c.get(page).content.decode(), page


class TestConnectClicked:
    URL = "/cauldron/api/mcp/connect-clicked/"

    @pytest.mark.parametrize("target", ["claude_ai", "desktop", "code"])
    def test_records_only_the_target(self, db, events, target):
        r = Client().post(self.URL, {"target": target}, content_type="application/json")
        assert r.status_code == 204
        assert events == [("mcp_connect_clicked", {"target": target})]

    def test_signed_in_needs_csrf_and_sends_no_user_data(self, alice, events):
        c = Client(enforce_csrf_checks=True)
        c.force_login(alice)
        assert c.post(self.URL, {"target": "code"}, content_type="application/json").status_code == 403
        c.get(MCP, HTTP_ACCEPT=HTML)  # sets the csrftoken cookie
        token = c.cookies["csrftoken"].value
        r = c.post(self.URL, {"target": "code"}, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        assert r.status_code == 204
        assert events[-1] == ("mcp_connect_clicked", {"target": "code"})

    @pytest.mark.parametrize("body", [{"target": "alice@example.com"}, {}, {"target": ["code"]}])
    def test_anything_else_is_400_and_not_tracked(self, db, events, body):
        assert Client().post(self.URL, body, content_type="application/json").status_code == 400
        assert events == []


class TestLegalUpdate:
    def test_versions_bumped_and_pages_match(self, db):
        assert legal.CURRENT_VERSIONS[legal.TERMS] == "1.2"
        assert legal.CURRENT_VERSIONS[legal.PRIVACY] == "1.3"
        privacy = Client().get("/cauldron/privacy/").content.decode()
        terms = Client().get("/cauldron/terms/").content.decode()
        assert "Version 1.3" in privacy and "AI assistants you connect" in privacy
        assert "Version 1.2" in terms and "7. AI assistants" in terms

    def test_users_on_the_old_versions_are_asked_again(self, db):
        user = _user("old", consent=False)
        from the_cauldron.models import LegalAcceptance

        LegalAcceptance.objects.create(user=user, document=legal.TERMS, version="1.1")
        LegalAcceptance.objects.create(user=user, document=legal.PRIVACY, version="1.2")
        assert set(legal.pending_documents(user)) == {legal.TERMS, legal.PRIVACY}
