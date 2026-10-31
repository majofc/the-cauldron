"""Login and sign-up forms for The Cauldron (#72), plugged into allauth via
``ACCOUNT_FORMS``."""

from datetime import datetime, timedelta, timezone

from allauth.account.app_settings import EmailVerificationMethod
from allauth.account.forms import LoginForm, SignupForm
from allauth.account.models import EmailAddress
from django import forms
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _

from .services import age_gate, legal


# Accounts that existed before open sign-up (#72) were never asked to verify.
SIGNUP_LAUNCH = datetime(2026, 10, 20, tzinfo=timezone.utc)


class CauldronLoginForm(LoginForm):
    def clean(self):
        cleaned = super().clean()
        user = getattr(self, 'user', None)
        login = getattr(self, '_login', None)
        # Keep signing in the pre-launch accounts as before (admin-made, no
        # EmailAddress row). Anything newer without a verified email verifies
        # first: console invites accept any address, so it may not be theirs.
        if (
            user and login and user.date_joined < SIGNUP_LAUNCH
            and not EmailAddress.objects.filter(user=user).exists()
        ):
            login.email_verification = EmailVerificationMethod.NONE
        return cleaned


class CauldronSignupForm(SignupForm):
    # `first_name` so allauth stores it and builds the username from it: the
    # username is public in the Coven, and built from the email it would leak
    # most of the address.
    first_name = forms.CharField(
        label=_('Display name'),
        max_length=150,
        widget=forms.TextInput(attrs={'autocomplete': 'nickname'}),
    )
    # The Cauldron is for adults (#115): no birth year, or under 18, no account.
    birth_year = forms.IntegerField(
        label=_('Birth year'),
        min_value=1900,
        widget=forms.NumberInput(attrs={'inputmode': 'numeric', 'placeholder': '1995'}),
        error_messages={'required': _('Your birth year is required.')},
    )
    accept_terms = forms.BooleanField(
        required=True,
        error_messages={'required': _('You must accept the Terms and the Privacy Notice.')},
    )

    def clean_birth_year(self):
        year = self.cleaned_data['birth_year']
        if year > dj_timezone.localdate().year:
            raise forms.ValidationError(_('Enter a year between 1900 and this year.'))
        if not age_gate.is_adult(year):
            raise forms.ValidationError(_('The Cauldron is only for people 18 and older.'))
        return year

    def clean_email(self):
        # A sign-up left unverified past the link's lifetime gives the address
        # back (#72): otherwise whoever registered it first would hold it forever.
        from django.conf import settings

        from .auth_adapters import discard_pending_signups

        discard_pending_signups(
            self.data.get('email'), older_than=timedelta(days=settings.ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS)
        )
        return super().clean_email()

    def custom_signup(self, request, user):
        # Runs only for a new account: an email that already exists is never
        # saved (allauth emails that inbox instead, so the response doesn't
        # reveal the account). The ticked box is the acceptance, timestamped.
        from .models import CauldronProfile

        legal.accept(user, legal.GATED)
        CauldronProfile.objects.update_or_create(user=user, defaults={'birth_year': self.cleaned_data['birth_year']})
