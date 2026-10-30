"""Open sign-up for The Cauldron (#72): email + password with mandatory
verification, Google, password reset, abuse limits and the old logins."""

import re
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

import pytest
from allauth.account.models import EmailAddress
from allauth.core import context
from allauth.socialaccount.adapter import get_adapter as get_social_adapter
from allauth.socialaccount.helpers import complete_social_login
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import mail
from django.core.cache import cache
from django.test import Client, RequestFactory
from django.utils import timezone

from the_cauldron.models import LegalAcceptance, Program
from the_cauldron.services import legal
from tools_communication.models import CommunicationLog

User = get_user_model()
PRE_LAUNCH = datetime(2026, 1, 1, tzinfo=dt_timezone.utc)  # before open sign-up

REGISTER = "/cauldron/register/"
LOGIN = "/cauldron/login/"
RESET = "/cauldron/password-reset/"
SENT = "/cauldron/account/confirm-email/"
GOOGLE_APP = {"google": {"APP": {"client_id": "test-id", "secret": "test-secret"}, "SCOPE": ["profile", "email"]}}


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    # allauth counts rate limits in the cache; never carry them across tests.
    cache.clear()
    yield
    cache.clear()


def _signup_data(**overrides):
    data = {
        "email": "ada@example.com",
        "first_name": "Ada",
        "password1": "s3cret-Cauldron!",
        "password2": "s3cret-Cauldron!",
        "birth_year": "1990",
        "accept_terms": "on",
        "website": "",
    }
    data.update(overrides)
    return data


def _link(pattern):
    body = mail.outbox[-1].alternatives[0][0]
    match = re.search(pattern, body)
    assert match, body
    return match.group(0)


def _confirm_url():
    return _link(r"/cauldron/account/confirm-email/[^/\s\"<]+/")


def _logged_in(client):
    return "_auth_user_id" in client.session


@pytest.mark.django_db
class TestRegister:
    def test_register_creates_unverified_account_and_sends_verification(self):
        client = Client()
        resp = client.post(REGISTER, _signup_data())
        assert resp.status_code == 302 and resp["Location"] == SENT

        user = User.objects.get(email="ada@example.com")
        assert user.first_name == "Ada"
        assert not EmailAddress.objects.get(user=user).verified
        assert not _logged_in(client)
        assert len(mail.outbox) == 1 and mail.outbox[0].to == ["ada@example.com"]
        assert "/cauldron/account/confirm-email/" in mail.outbox[0].alternatives[0][0]
        log = CommunicationLog.objects.get(recipient="ada@example.com")
        assert log.requested_by == "the_cauldron" and log.status == "success"

    def test_sign_up_stays_inactive_so_no_other_login_lets_it_in(self):
        Client().post(REGISTER, _signup_data())
        user = User.objects.get(email="ada@example.com")
        assert not user.is_active
        # Domum, Loom, the console and admin all go through authenticate().
        assert not Client().login(username=user.username, password="s3cret-Cauldron!")
        assert not Client().login(username="ada@example.com", password="s3cret-Cauldron!")

    def test_username_comes_from_the_display_name_not_the_email(self):
        Client().post(REGISTER, _signup_data(email="maria.lopez1990@example.com", first_name="Mar"))
        user = User.objects.get(email="maria.lopez1990@example.com")
        assert user.first_name == "Mar"
        assert "lopez" not in user.username and "1990" not in user.username

    def test_terms_acceptance_is_required_and_timestamped(self):
        resp = Client().post(REGISTER, _signup_data(accept_terms=""))
        assert resp.status_code == 200
        assert not User.objects.filter(email="ada@example.com").exists()

        Client().post(REGISTER, _signup_data())
        user = User.objects.get(email="ada@example.com")
        rows = LegalAcceptance.objects.filter(user=user)
        assert {r.document for r in rows} == set(legal.GATED)
        assert all(r.accepted_at and r.version == legal.CURRENT_VERSIONS[r.document] for r in rows)
        assert legal.pending_documents(user) == []

    def test_weak_or_mismatched_password_is_rejected(self):
        assert Client().post(REGISTER, _signup_data(password1="123", password2="123")).status_code == 200
        assert Client().post(REGISTER, _signup_data(password2="other-Pass-99")).status_code == 200
        assert not User.objects.filter(email="ada@example.com").exists()

    def test_duplicate_email_in_any_case_looks_like_a_fresh_signup(self):
        User.objects.create_user("ada", "ada@example.com", "pw-original-1")
        resp = Client().post(REGISTER, _signup_data(email="ADA@Example.com"))
        # Same answer as a brand-new signup: nothing reveals the account exists…
        assert resp.status_code == 302 and resp["Location"] == SENT
        assert User.objects.filter(email__iexact="ada@example.com").count() == 1
        # …and the real owner is told by email instead.
        assert mail.outbox and mail.outbox[-1].to[0].lower() == "ada@example.com"

    def test_email_links_only_the_real_link(self):
        Client().post(REGISTER, _signup_data(first_name="claim-prize.evil.example"))
        body = mail.outbox[-1].alternatives[0][0]
        assert '<a href="http://testserver/cauldron/account/confirm-email/' in body
        assert body.count("<a ") == 1

    def test_honeypot_creates_nothing(self):
        resp = Client().post(REGISTER, _signup_data(website="http://spam.example"))
        assert resp.status_code == 302
        assert not User.objects.filter(email="ada@example.com").exists()
        assert mail.outbox == []

    def test_new_user_starts_without_a_program(self):
        Client().post(REGISTER, _signup_data())
        user = User.objects.get(email="ada@example.com")
        assert not Program.objects.filter(user=user).exists()


@pytest.mark.django_db
class TestVerification:
    def test_unverified_account_cannot_sign_in(self):
        Client().post(REGISTER, _signup_data())
        client = Client()
        resp = client.post(LOGIN, {"login": "ada@example.com", "password": "s3cret-Cauldron!"})
        assert resp.status_code == 302 and resp["Location"] == SENT
        assert not _logged_in(client)
        page = client.get(SENT).content.decode()
        assert "Check your email" in page

    def test_link_verifies_and_signs_in_then_forge_and_grimoire_open(self):
        client = Client()
        client.post(REGISTER, _signup_data())
        url = _confirm_url()
        assert "Confirm and enter" in client.get(url).content.decode()
        resp = client.post(url)
        assert resp.status_code == 302
        assert EmailAddress.objects.get(email="ada@example.com").verified
        assert User.objects.get(email="ada@example.com").is_active
        assert _logged_in(client)
        # A new user starts on onboarding (equipment → Trial), not on a program.
        forge = client.get("/cauldron/forge/", follow=True)
        assert forge.status_code == 200 and forge.redirect_chain[-1][0] == "/cauldron/forge/equipment/"
        assert client.get("/cauldron/grimoire/").status_code == 200

    def test_confirming_never_revives_an_account_an_admin_switched_off(self):
        # Never signed in, unverified email, switched off by an admin: not a sign-up.
        user = User.objects.create_user("off", "off@example.com", "pw-switched-1", is_active=False)
        address = EmailAddress.objects.create(user=user, email="off@example.com", primary=True)
        from allauth.account.models import EmailConfirmationHMAC

        Client().post(f"/cauldron/account/confirm-email/{EmailConfirmationHMAC(address).key}/")
        user.refresh_from_db()
        assert EmailAddress.objects.get(pk=address.pk).verified
        assert not user.is_active

    def test_stale_unverified_sign_up_gives_the_email_back(self):
        Client().post(REGISTER, _signup_data(first_name="Squatter"))
        User.objects.filter(email="ada@example.com").update(date_joined=timezone.now() - timedelta(days=2))
        Client().post(REGISTER, _signup_data(first_name="Ada"))
        user = User.objects.get(email="ada@example.com")
        assert user.first_name == "Ada"

    def test_fresh_unverified_sign_up_is_not_replaced(self):
        Client().post(REGISTER, _signup_data(first_name="First"))
        Client().post(REGISTER, _signup_data(first_name="Second"))
        assert User.objects.get(email="ada@example.com").first_name == "First"

    def test_verified_account_signs_in_with_email(self):
        client = Client()
        client.post(REGISTER, _signup_data())
        client.post(_confirm_url())
        fresh = Client()
        fresh.post(LOGIN, {"login": "ADA@example.com", "password": "s3cret-Cauldron!"})
        assert _logged_in(fresh)

    def test_tampered_link_is_rejected_with_a_resend_path(self):
        client = Client()
        client.post(REGISTER, _signup_data())
        url = _confirm_url()
        bad = url[:-2] + ("A" if url[-2] != "A" else "B") + "/"
        page = client.get(bad).content.decode()
        assert "This link no longer works" in page and LOGIN in page
        client.post(bad)
        assert not EmailAddress.objects.get(email="ada@example.com").verified

    def test_expired_link_is_rejected(self):
        client = Client()
        client.post(REGISTER, _signup_data())
        url = _confirm_url()
        later = time.time() + 2 * 86400
        with mock.patch("django.core.signing.time.time", return_value=later):
            assert "This link no longer works" in client.get(url).content.decode()
            client.post(url)
        assert not EmailAddress.objects.get(email="ada@example.com").verified

    def test_resend_on_sign_in_is_rate_limited(self):
        Client().post(REGISTER, _signup_data())
        sent = len(mail.outbox)
        creds = {"login": "ada@example.com", "password": "s3cret-Cauldron!"}
        Client().post(LOGIN, creds)
        Client().post(LOGIN, creds)
        # The signup email was the last one; attempts inside the cooldown don't send more.
        assert len(mail.outbox) == sent


@pytest.mark.django_db
class TestPasswordReset:
    def test_unknown_email_gets_no_mail(self):
        Client().post(RESET, {"email": "nobody@example.com"})
        assert mail.outbox == []

    def test_known_and_unknown_emails_get_the_same_answer(self):
        User.objects.create_user("ada", "ada@example.com", "pw-original-1")
        known = Client().post(RESET, {"email": "ada@example.com"})
        unknown = Client().post(RESET, {"email": "nobody@example.com"})
        assert known.status_code == unknown.status_code == 302
        assert known["Location"] == unknown["Location"]
        done = Client().get(known["Location"]).content.decode()
        assert "If that email belongs to an account" in done

    def test_reset_end_to_end(self):
        User.objects.create_user("ada", "ada@example.com", "pw-original-1")
        Client().post(RESET, {"email": "ada@example.com"})
        link = _link(r"/cauldron/account/password/reset/key/[^/\s\"<]+/")
        client = Client()
        form_url = client.get(link)["Location"]
        resp = client.post(form_url, {"password1": "brand-New-pass-7", "password2": "brand-New-pass-7"})
        assert resp.status_code == 302
        fresh = Client()
        fresh.post(LOGIN, {"login": "ada", "password": "brand-New-pass-7"})
        assert _logged_in(fresh)
        assert EmailAddress.objects.get(email="ada@example.com").verified

    def test_used_or_bad_reset_link_says_so(self):
        page = Client().get("/cauldron/account/password/reset/key/1-bad-token/", follow=True).content.decode()
        assert "This link no longer works" in page


@pytest.mark.django_db
class TestRateLimits:
    def test_register_is_limited_per_ip(self):
        client = Client()
        codes = [client.post(REGISTER, _signup_data(email=f"u{i}@example.com")).status_code for i in range(11)]
        assert codes[:10] == [302] * 10 and codes[10] == 429

    def test_reset_is_limited_per_email(self):
        User.objects.create_user("ada", "ada@example.com", "pw-original-1")
        codes = [Client().post(RESET, {"email": "ada@example.com"}).status_code for _ in range(4)]
        assert codes[:3] == [302] * 3 and codes[3] == 429


@pytest.mark.django_db
class TestExistingLogins:
    def test_legacy_account_without_email_record_signs_in_by_username_and_email(self):
        User.objects.create_user("veteran", "vet@example.com", "pw-veteran-1", date_joined=PRE_LAUNCH)
        by_username = Client()
        by_username.post(LOGIN, {"login": "veteran", "password": "pw-veteran-1"})
        by_email = Client()
        by_email.post(LOGIN, {"login": "vet@example.com", "password": "pw-veteran-1"})
        assert _logged_in(by_username) and _logged_in(by_email)

    def test_account_made_after_launch_without_email_record_must_verify(self):
        User.objects.create_user("newbie", "new@example.com", "pw-newbie-12")
        client = Client()
        resp = client.post(LOGIN, {"login": "newbie", "password": "pw-newbie-12"})
        assert resp.status_code == 302 and resp["Location"] == SENT
        assert not _logged_in(client)

    def test_bad_password_is_refused(self):
        User.objects.create_user("veteran", "vet@example.com", "pw-veteran-1", date_joined=PRE_LAUNCH)
        client = Client()
        resp = client.post(LOGIN, {"login": "veteran", "password": "nope"})
        assert resp.status_code == 200 and not _logged_in(client)

    def test_admin_login_still_works(self):
        User.objects.create_superuser("root", "root@example.com", "pw-root-123")
        client = Client()
        resp = client.post("/admin/login/?next=/admin/", {"username": "root", "password": "pw-root-123"})
        assert resp.status_code == 302 and _logged_in(client)

    def test_unsafe_next_is_ignored(self):
        User.objects.create_user("veteran", "vet@example.com", "pw-veteran-1", date_joined=PRE_LAUNCH)
        resp = Client().post(
            LOGIN, {"login": "veteran", "password": "pw-veteran-1", "next": "https://evil.example/"}
        )
        assert resp.status_code == 302 and "evil.example" not in resp["Location"]

    def test_login_page_links_to_sign_up_and_reset(self):
        page = Client().get(LOGIN).content.decode()
        assert REGISTER in page and RESET in page
        assert "Continue with Google" not in page  # no Google credentials configured


def _google_request():
    request = RequestFactory().get("/cauldron/account/google/login/callback/")
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    MessageMiddleware(lambda r: None).process_request(request)
    request.user = AnonymousUser()
    return request


def _google_login(email, *, verified=True, uid="google-uid-1"):
    """Run allauth's social-login flow as if Google had just answered."""
    request = _google_request()
    provider = get_social_adapter().get_provider(request, "google")
    sociallogin = SocialLogin(
        provider=provider,
        account=SocialAccount(provider="google", uid=uid, extra_data={"email": email, "name": "Ada"}),
        user=User(email=email, first_name="Ada"),
        email_addresses=[EmailAddress(email=email, verified=verified, primary=True)],
    )
    sociallogin.state = {"process": "login"}
    with context.request_context(request):
        complete_social_login(request, sociallogin)
    return request


@pytest.mark.django_db
class TestGoogle:
    @pytest.fixture(autouse=True)
    def _google_configured(self, settings):
        settings.SOCIALACCOUNT_PROVIDERS = GOOGLE_APP

    def test_button_shows_when_configured(self):
        assert "Continue with Google" in Client().get(LOGIN).content.decode()
        assert "Continue with Google" in Client().get(REGISTER).content.decode()

    def test_first_google_sign_in_creates_a_verified_account_without_email_step(self):
        request = _google_login("grace@example.com")
        user = User.objects.get(email="grace@example.com")
        assert request.user == user
        assert EmailAddress.objects.get(user=user).verified
        assert mail.outbox == []
        # Terms/Privacy come next: every Cauldron page is gated until accepted.
        assert legal.pending_documents(user) == list(legal.GATED)
        client = Client()
        client.force_login(user)
        resp = client.get("/cauldron/forge/")
        assert resp.status_code == 302 and resp["Location"].startswith("/cauldron/accept/")

    def test_google_links_to_the_existing_verified_local_account(self):
        local = User.objects.create_user("ada", "ada@example.com", "pw-original-1")
        EmailAddress.objects.create(user=local, email="ada@example.com", verified=True, primary=True)
        request = _google_login("ADA@example.com")
        assert request.user == local
        assert User.objects.filter(email__iexact="ada@example.com").count() == 1
        assert SocialAccount.objects.get(provider="google").user == local

    def test_google_replaces_an_unverified_sign_up_holding_its_email(self):
        # Someone registers with the owner's email and waits (never verified)…
        Client().post(REGISTER, _signup_data(email="grace@example.com"))
        squatter_pk = User.objects.get(email="grace@example.com").pk
        # …the real owner signs in with Google: the squatter's account is gone.
        request = _google_login("grace@example.com")
        assert not User.objects.filter(pk=squatter_pk).exists()
        assert request.user.email == "grace@example.com" and request.user.is_active

    def test_linking_by_email_removes_the_password_and_tells_the_owner(self):
        local = User.objects.create_user("ada", "ada@example.com", "pw-original-1")
        EmailAddress.objects.create(user=local, email="ada@example.com", verified=True, primary=True)
        _google_login("ada@example.com")
        local.refresh_from_db()
        assert not local.has_usable_password()
        assert mail.outbox[-1].to == ["ada@example.com"]
        assert "/cauldron/password-reset/" in mail.outbox[-1].alternatives[0][0]

    def test_google_never_links_to_an_account_without_an_email_record(self):
        # e.g. a console invite registered with an address nobody verified.
        planted = User.objects.create_user("planted", "grace@example.com", "pw-attacker-1")
        request = _google_login("grace@example.com")
        assert request.user != planted
        assert not SocialAccount.objects.filter(user=planted).exists()
        planted.refresh_from_db()
        assert planted.has_usable_password()

    def test_unverified_google_email_does_not_link(self):
        local = User.objects.create_user("ada", "ada@example.com", "pw-original-1")
        request = _google_login("ada@example.com", verified=False)
        assert request.user != local
        assert not SocialAccount.objects.filter(user=local).exists()

    def test_already_linked_google_is_refused_once_the_user_is_staff(self):
        staff = User.objects.create_user("boss", "boss@example.com", "pw-boss-1234", is_staff=True)
        SocialAccount.objects.create(user=staff, provider="google", uid="google-uid-1")
        request = _google_login("boss@example.com")
        assert request.user != staff

    def test_google_never_signs_in_to_a_staff_account(self):
        staff = User.objects.create_user("boss", "boss@example.com", "pw-boss-1234", is_staff=True)
        request = _google_login("boss@example.com")
        assert request.user != staff
        assert not SocialAccount.objects.filter(user=staff).exists()
