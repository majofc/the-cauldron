import uuid
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import get_language

from .forge_i18n import forge_catalog, forge_strings, showcase_tree
from .grimoire_i18n import grimoire_strings
from .language import force_spanish, public_seo, remember_language
from .models import Casting, GrimoirePreferences, Program, Spell, UserEquipmentProfile
from .nutrition_i18n import nutrition_strings
from .services import grimoire, legal, nutrition

# URL slug → the ``data-tab`` key forge.js switches on. Mirrored client-side by
# TAB_SLUGS in forge.js; keep the two in step.
FORGE_SECTIONS = {
    'equipment': 'equipment',
    'the-trial': 'trial',
    'today': 'today',
    'exercises': 'exercises',
    'progress': 'progress',
    'about': 'about',
}

# Sections that only make sense once an earlier step is done, mapped to the
# steps they depend on. A direct load of one of these while the user is still
# stuck on a listed step bounces them to that step; in-app tab clicks are never
# gated and always open the panel with its own empty state.
SECTION_REQUIREMENTS = {
    'the-trial': ('equipment',),
    'today': ('equipment', 'the-trial'),
}


def cauldron_page(view):
    """``login_required`` plus the legal gate (#84): a signed-in user who hasn't
    accepted the current terms and privacy notice is sent to the acceptance
    page first, and comes back here afterwards."""

    @wraps(view)
    @login_required(login_url='the_cauldron:login')
    @remember_language
    def wrapped(request, *args, **kwargs):
        if legal.pending_documents(request.user):
            query = urlencode({'next': request.get_full_path()})
            return redirect(f"{reverse('the_cauldron:legal-accept')}?{query}")
        return view(request, *args, **kwargs)

    return wrapped


def _landing(request):
    return render(request, 'the_cauldron/landing.html', {
        **public_seo(request, 'landing'),
        'showcase_tree': showcase_tree(),
    })


@remember_language
def landing_view(request):
    return _landing(request)


def landing_es_view(request):
    """/cauldron/es/ — the landing, always in Spanish (indexable)."""
    force_spanish(request)
    return _landing(request)


def _legal_page(request, page):
    return render(request, f'the_cauldron/legal/{page}.html', public_seo(request, page))


@remember_language
def legal_page_view(request, page):
    """Terms or privacy notice, in the user's language."""
    return _legal_page(request, page)


def legal_page_es_view(request, page):
    """Terms or privacy notice at their /cauldron/es/ URL, always in Spanish."""
    force_spanish(request)
    return _legal_page(request, page)


def _first_unready_section(user):
    """The earliest step the user still has to complete, or None when set up.

    Follows the Forge flow: choose gear → take the Trial → train. The profile
    row is created on the first API read, so readiness keys off ``configured_at``
    (set when the Equipment form is saved), not the row's existence.
    """
    configured = UserEquipmentProfile.objects.filter(
        user=user, configured_at__isnull=False
    ).exists()
    if not configured:
        return 'equipment'
    if not Program.objects.filter(user=user, is_active=True).exists():
        return 'the-trial'
    return None


@cauldron_page
def forge_view(request, section=None):
    """The Forge — adaptive calisthenics. Single page; sections driven by JS
    against the /cauldron/api/ endpoints.

    Each section has its own URL (``/cauldron/forge/<slug>/``). The bare
    ``/cauldron/forge/`` resolves to whichever step the user is on.
    """
    unready = _first_unready_section(request.user)

    if section is None:
        return redirect('the_cauldron:forge-section', section=unready or 'today')

    if section not in FORGE_SECTIONS:
        raise Http404('Unknown Forge section.')

    if unready in SECTION_REQUIREMENTS.get(section, ()):
        return redirect('the_cauldron:forge-section', section=unready)

    return render(
        request,
        'the_cauldron/forge.html',
        {
            'initial_tab': FORGE_SECTIONS[section],
            'forge_strings': forge_strings(),
            'forge_catalog': forge_catalog(),
        },
    )


def _grimoire_context(request, **extra):
    """What every Grimoire page needs: the JS strings and the user's unit
    system ride in with the page, so nothing flickers on load."""
    prefs, _created = GrimoirePreferences.objects.get_or_create(user=request.user)
    return {'grimoire_strings': grimoire_strings(), 'unit_system': prefs.unit_system, **extra}


@cauldron_page
def grimoire_view(request):
    """The Grimoire — recipe book: spells list + reagents, driven by the
    /cauldron/api/grimoire/ endpoints (grimoire.js)."""
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(request, page='list'))


@cauldron_page
def grimoire_spell_view(request, spell_uuid):
    """One spell on its own page (shareable URL, browser back works).

    Same visibility as the API: the owner's spells and public ones; anything
    else is a 404, so a private spell's existence never leaks. The content is
    rendered by grimoire.js from the API.
    """
    spell = get_object_or_404(grimoire.readable_spells(request.user), uuid=spell_uuid)
    title, _description, _steps = grimoire.localized_text(spell, get_language())
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(
        request, page='spell', spell_uuid=str(spell.uuid), spell_title=title,
    ))


@cauldron_page
def grimoire_cast_view(request, spell_uuid):
    """Log a casting of a spell (#68) on a full page. Any spell the user can
    read — their own or a public one — can be cast; anything else is a 404."""
    spell = get_object_or_404(grimoire.readable_spells(request.user), uuid=spell_uuid)
    title, _description, _steps = grimoire.localized_text(spell, get_language())
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(
        request, page='cast', spell_uuid=str(spell.uuid), spell_title=title,
    ))


@cauldron_page
def grimoire_casting_edit_view(request, casting_uuid):
    """Edit one of your own castings; anyone else's is a 404."""
    casting = get_object_or_404(Casting.objects.select_related('spell'), uuid=casting_uuid, user=request.user)
    title = casting.spell_title  # snapshot: the spell is gone or no longer readable
    spell = casting.spell
    if spell is not None and grimoire.readable_spells(request.user).filter(pk=spell.pk).exists():
        title = grimoire.localized_text(spell, get_language())[0]
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(
        request, page='cast', casting_uuid=str(casting.uuid), spell_title=title,
    ))


@cauldron_page
def grimoire_reagent_view(request, reagent_uuid):
    """One ingredient on its own page: full nutrient profile and measures.
    Visible like the API (approved + the user's own), else 404."""
    reagent = get_object_or_404(grimoire.visible_reagents(request.user), uuid=reagent_uuid)
    is_es = (get_language() or '').startswith('es')
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(
        request, page='reagent-view', reagent_uuid=str(reagent.uuid),
        spell_title=(is_es and reagent.name_es) or reagent.name,
    ))


@cauldron_page
def grimoire_reagent_new_view(request, reagent_uuid=None):
    """Add an ingredient (with its nutrients) on a full page, or edit one of
    your own that isn't approved yet (404 otherwise). ``?name=`` prefills the
    name of a new one, e.g. from a search that found nothing."""
    if reagent_uuid is not None:
        reagent = get_object_or_404(grimoire.visible_reagents(request.user), uuid=reagent_uuid)
        if not grimoire.can_edit_reagent(request.user, reagent):
            raise Http404
        reagent_uuid = str(reagent.uuid)
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(
        request, page='reagent', reagent_uuid=reagent_uuid,
        prefill_name=request.GET.get('name', '')[:120],
    ))


@cauldron_page
def grimoire_edit_view(request, spell_uuid=None):
    """Write a new spell, or edit one of your own, on a full page.

    Editing is owner-only: anyone else gets a 404 (a public spell is read on
    its page and copied with "Make it mine", never edited in place).
    """
    title = None
    if spell_uuid is not None:
        spell = get_object_or_404(Spell, uuid=spell_uuid, owner=request.user)
        spell_uuid, title = str(spell.uuid), spell.title
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(
        request, page='edit', spell_uuid=spell_uuid, spell_title=title,
    ))


# ── Daily nutrition (#80–#84) ───────────────────────────────────────────────


def _safe_next(request):
    target = request.GET.get('next', '')
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse('the_cauldron:landing')


def _nutrition_context(request, page, **extra):
    prefs, _created = GrimoirePreferences.objects.get_or_create(user=request.user)
    return {
        'page': page,
        'nutrition_strings': nutrition_strings(),
        'unit_system': prefs.unit_system,
        'legal_versions': legal.CURRENT_VERSIONS,
        **extra,
    }


@login_required(login_url='the_cauldron:login')
def legal_accept_view(request):
    """Accept the current terms and privacy notice (#84). The form posts to
    ``api/legal/accept/``; with nothing pending this just moves on."""
    target = _safe_next(request)
    pending = legal.pending_documents(request.user)
    if not pending:
        return redirect(target)
    return render(request, 'the_cauldron/nutrition.html', _nutrition_context(
        request, 'accept', pending=pending, next_url=target,
    ))


@cauldron_page
def profile_view(request):
    """The Cauldron profile (#80, #82): timezone, sensitive-data consent,
    daily nutrient goals and adherence. Driven by nutrition.js."""
    return render(request, 'the_cauldron/nutrition.html', _nutrition_context(
        request, 'profile', has_consent=legal.has_sensitive_consent(request.user),
    ))


@cauldron_page
def journal_view(request):
    """One day of the meal log and the ritual planner (#81, #83).
    ``?date=YYYY-MM-DD`` (default today, user's timezone); ``?log_spell=`` /
    ``?plan_spell=`` preselect a readable spell in the add forms."""
    user = request.user
    try:
        day = nutrition.parse_day(request.GET.get('date'), user)
    except nutrition.NutritionInputError:
        day = nutrition.today_for(user)
    prefill = None
    for param, mode in (('log_spell', 'log'), ('plan_spell', 'plan')):
        raw = request.GET.get(param)
        if not raw:
            continue
        spell = grimoire.readable_spells(user).filter(uuid=raw).first() if _is_uuid(raw) else None
        if spell is not None:
            prefill = {
                'mode': mode,
                'uuid': str(spell.uuid),
                'title': grimoire.localized_text(spell, get_language())[0],
                'servings': spell.servings,
            }
        break
    return render(request, 'the_cauldron/nutrition.html', _nutrition_context(
        request, 'journal', has_consent=legal.has_sensitive_consent(user),
        day=day.isoformat(), today=nutrition.today_for(user).isoformat(), prefill=prefill,
        prev_day=(day - timedelta(days=1)).isoformat(), next_day=(day + timedelta(days=1)).isoformat(),
    ))


def _is_uuid(value):
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True
