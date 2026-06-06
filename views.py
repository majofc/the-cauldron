from django.contrib.auth.decorators import login_required
from django.shortcuts import render


def landing_view(request):
    return render(request, 'the_cauldron/landing.html')


@login_required(login_url='the_cauldron:login')
def forge_view(request):
    """The Forge — adaptive calisthenics. Single page; sections driven by JS
    against the /cauldron/api/ endpoints."""
    return render(request, 'the_cauldron/forge.html')
