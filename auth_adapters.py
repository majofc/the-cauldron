"""django-allauth hooks for The Cauldron's open sign-up (#72).

Only The Cauldron uses allauth, so these adapters keep every redirect inside
``/cauldron/`` and send every account email through ``tools_communication`` (so
it lands in ``CommunicationLog`` like the rest of the site's mail).
"""

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.sites.shortcuts import get_current_site
from django.shortcuts import redirect
from django.template.defaultfilters import linebreaks
from django.urls import reverse
from django.utils.html import escape, format_html

from tools_communication.services import send_email


def is_pending_verification(user):
    """A self-registered account that has not verified its email yet."""
    from .models import CauldronProfile

    return not user.is_active and CauldronProfile.objects.filter(user=user, pending_verification=True).exists()


def discard_pending_signups(email, older_than=None):
    """Delete unverified sign-ups holding ``email`` (all, or only those older
    than ``older_than``), so a squatter who never verifies can't keep the real
    owner off their own address. A pending account has no data yet."""
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    users = get_user_model().objects.filter(
        email__iexact=(email or '').strip(), is_active=False, cauldron_profile__pending_verification=True
    )
    if older_than is not None:
        users = users.filter(date_joined__lt=timezone.now() - older_than)
    users.delete()


class CauldronAccountAdapter(DefaultAccountAdapter):
    def save_user(self, request, user, form, commit=True):
        """A sign-up starts **inactive** until its email is verified
        (``signals.activate_on_email_confirmed``). The session is site-wide, so
        an inactive account is what keeps every other login (Domum, Loom, the
        console, admin) from letting an unverified sign-up in."""
        from .models import CauldronProfile

        user = super().save_user(request, user, form, commit=False)
        user.is_active = False
        if commit:
            user.save()
            CauldronProfile.objects.update_or_create(user=user, defaults={'pending_verification': True})
        return user

    def pre_login(self, request, user, **kwargs):
        """A pending sign-up goes on to allauth's mandatory verification stage
        (which sends the link, within the cooldown, and keeps the login for
        after the click) instead of the "account inactive" page. It can't get
        further: that stage stops it until the email is verified."""
        if is_pending_verification(user):
            return None
        return super().pre_login(request, user, **kwargs)

    # The journal is the app's home (#102): a sign-in without ?next= lands there.
    def get_login_redirect_url(self, request):
        return reverse('the_cauldron:journal')

    def get_signup_redirect_url(self, request):
        return reverse('the_cauldron:journal')

    def get_logout_redirect_url(self, request):
        return reverse('the_cauldron:landing')

    def get_email_verification_redirect_url(self, email_address):
        # Logged in by ACCOUNT_LOGIN_ON_EMAIL_CONFIRMATION → straight in; a link
        # opened in another browser → sign in first.
        if self.request.user.is_authenticated:
            return reverse('the_cauldron:journal')
        return reverse('the_cauldron:login')

    def send_mail(self, template_prefix, email, context):
        """Render allauth's (translated) plain-text email and send it as HTML
        through ``tools_communication``. Never raises: a failed send is logged."""
        msg = self.render_mail(template_prefix, email, {
            'request': self.request,
            'email': email,
            'current_site': get_current_site(self.request),
            **context,
        })
        # Only allauth's own links become clickable: the text also carries
        # what the person signing up typed (their name), which must stay text.
        body = linebreaks(escape(msg.body))
        for key in ('activate_url', 'password_reset_url', 'signup_url'):
            url = context.get(key)
            if url:
                body = body.replace(escape(url), format_html('<a href="{0}">{0}</a>', url))
        send_email(to=email, requested_by='the_cauldron', subject=msg.subject, body_html=body)


class CauldronSocialAccountAdapter(DefaultSocialAccountAdapter):
    def can_authenticate_by_email(self, login, email):
        """Google sign-in with a Google-verified email signs in to the local
        account with that email only when the local account has *also* verified
        it. An unverified one may have been created with someone else's email
        (console invites accept any address) and its password would survive the
        link. Staff accounts never sign in with Google."""
        if not super().can_authenticate_by_email(login, email):
            return False
        from allauth.account.models import EmailAddress
        from allauth.account.utils import filter_users_by_email

        users = filter_users_by_email(email)
        return bool(users) and all(
            not _is_staff(user)
            and EmailAddress.objects.filter(user=user, email__iexact=email, verified=True).exists()
            for user in users
        )

    def pre_social_login(self, request, sociallogin):
        """- Staff accounts never sign in with Google, linked or connecting: a
          Google session must never reach the admin.
        - A Google-verified email frees itself from unverified sign-ups holding
          it, so the owner gets their account.
        - Linking Google to an existing account by email removes its password
          and tells the owner: whoever chose that password may not be them
          (someone can sign up with your email and you confirm it)."""
        super().pre_social_login(request, sociallogin)
        user = sociallogin.user if sociallogin.is_existing else request.user
        if user.is_authenticated and _is_staff(user):
            raise ImmediateHttpResponse(redirect('socialaccount_login_error'))
        if not sociallogin.is_existing:
            for address in sociallogin.email_addresses:
                if address.verified:
                    discard_pending_signups(address.email)
            return
        # allauth sets this when the account was found by its email (65.x).
        if getattr(sociallogin, '_did_authenticate_by_email', None) and user.has_usable_password():
            user.set_unusable_password()
            user.save(update_fields=['password'])
            from allauth.account.adapter import get_adapter as get_account_adapter

            get_account_adapter(request).send_mail('account/email/google_linked', user.email, {
                'user': user,
                'password_reset_url': request.build_absolute_uri(reverse('the_cauldron:password-reset')),
            })


def _is_staff(user):
    return user.is_staff or user.is_superuser
