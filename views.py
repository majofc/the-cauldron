from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect, render

from .models import Program, UserEquipmentProfile

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
