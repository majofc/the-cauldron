from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import get_language

from .grimoire_i18n import grimoire_strings
from .models import GrimoirePreferences, Program, Spell, UserEquipmentProfile
from .services import grimoire

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


def landing_view(request):
    return render(request, 'the_cauldron/landing.html')


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


@login_required(login_url='the_cauldron:login')
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
        {'initial_tab': FORGE_SECTIONS[section]},
    )


def _grimoire_context(request, **extra):
    """What every Grimoire page needs: the JS strings and the user's unit
    system ride in with the page, so nothing flickers on load."""
    prefs, _created = GrimoirePreferences.objects.get_or_create(user=request.user)
    return {'grimoire_strings': grimoire_strings(), 'unit_system': prefs.unit_system, **extra}


@login_required(login_url='the_cauldron:login')
def grimoire_view(request):
    """The Grimoire — recipe book: spells list + reagents, driven by the
    /cauldron/api/grimoire/ endpoints (grimoire.js)."""
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(request, page='list'))


@login_required(login_url='the_cauldron:login')
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


@login_required(login_url='the_cauldron:login')
def grimoire_reagent_view(request, reagent_uuid):
    """One ingredient on its own page: full nutrient profile and measures.
    Visible like the API (approved + the user's own), else 404."""
    reagent = get_object_or_404(grimoire.visible_reagents(request.user), uuid=reagent_uuid)
    is_es = (get_language() or '').startswith('es')
    return render(request, 'the_cauldron/grimoire.html', _grimoire_context(
        request, page='reagent-view', reagent_uuid=str(reagent.uuid),
        spell_title=(is_es and reagent.name_es) or reagent.name,
    ))


@login_required(login_url='the_cauldron:login')
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


@login_required(login_url='the_cauldron:login')
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
