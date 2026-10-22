"""The language switcher's memory, and The Cauldron's Spanish public URLs.

The switcher (arcano's base nav) posts to Django's ``set_language``, which only
sets the language cookie. For a signed-in user the choice is also kept on
``CauldronProfile.language`` so it follows them to another browser:

- a page served with a language cookie the profile doesn't hold yet → the
  cookie was an explicit choice; save it on the profile;
- a page served with no cookie (a fresh browser, e.g. right after logging in
  on another device) → apply the profile's language and set the cookie.

A cookie always wins over the profile, so a choice made in this browser is never
overridden; a browser that still carries an older cookie keeps it until the user
switches there.

Public pages (landing, terms, privacy) also live under ``/cauldron/es/``, always
in Spanish, with ``hreflang`` alternates and a self-referencing canonical.
"""

from functools import wraps

from django.conf import settings
from django.urls import reverse
from django.utils import translation

from the_cauldron.models import CauldronProfile

SUPPORTED = {code for code, _name in settings.LANGUAGES}

# Public page → (English URL name, Spanish URL name).
PUBLIC_PAGES = {
    "landing": ("the_cauldron:landing", "the_cauldron:landing-es"),
    "terms": ("the_cauldron:terms", "the_cauldron:terms-es"),
    "privacy": ("the_cauldron:privacy", "the_cauldron:privacy-es"),
}


def _activate(request, code):
    translation.activate(code)
    request.LANGUAGE_CODE = code


def remember_language(view):
    """Wrap a page view with the switcher's memory (see module docstring)."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return view(request, *args, **kwargs)
        cookie = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        cookie = cookie if cookie in SUPPORTED else None
        # Read-only unless there is a choice to save: a signed-in visitor who
        # never used The Cauldron doesn't get a profile row from a page view.
        saved = (
            CauldronProfile.objects.filter(user=request.user)
            .values_list("language", flat=True)
            .first()
        ) or ""

        apply = None
        if cookie is None and saved:
            apply = saved
            _activate(request, apply)
        elif cookie and cookie != saved:
            CauldronProfile.objects.update_or_create(user=request.user, defaults={"language": cookie})

        response = view(request, *args, **kwargs)
        if apply:
            response.set_cookie(
                settings.LANGUAGE_COOKIE_NAME,
                apply,
                max_age=settings.LANGUAGE_COOKIE_AGE,
                path=settings.LANGUAGE_COOKIE_PATH,
                domain=settings.LANGUAGE_COOKIE_DOMAIN,
                secure=settings.LANGUAGE_COOKIE_SECURE,
                httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
                samesite=settings.LANGUAGE_COOKIE_SAMESITE,
            )
        return response

    return wrapped


def force_spanish(request):
    """The /cauldron/es/ routes render in Spanish whatever the cookie says."""
    _activate(request, "es")


def public_seo(request, page):
    """``<head>`` alternates for a public page, plus where the switcher sends
    the reader: the same page in the other language."""
    en_name, es_name = PUBLIC_PAGES[page]
    en_url = request.build_absolute_uri(reverse(en_name))
    es_url = request.build_absolute_uri(reverse(es_name))
    on_es_route = request.resolver_match is not None and request.resolver_match.view_name == es_name
    is_es = (translation.get_language() or "").startswith("es")
    return {
        "seo_canonical": es_url if on_es_route else en_url,
        "seo_alternates": [("en", en_url), ("es", es_url), ("x-default", en_url)],
        # Only the other language's button is clickable, so one target is enough.
        "lang_switch_next": reverse(en_name) if is_es else reverse(es_name),
    }
