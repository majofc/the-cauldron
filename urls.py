from django.contrib.auth import views as auth_views
from django.views.generic import TemplateView
from django.urls import include, path
from rest_framework.routers import DefaultRouter, SimpleRouter

from . import api, grimoire_api, nutrition_api, oauth_views, views
from .mcp_server.protocol import McpView

app_name = "the_cauldron"

router = DefaultRouter()
router.register(r"patterns", api.PatternViewSet, basename="forge-pattern")
router.register(r"exercises", api.ExerciseViewSet, basename="forge-exercise")
router.register(r"sessions", api.SessionViewSet, basename="forge-session")
router.register(r"intake", nutrition_api.IntakeViewSet, basename="nutrition-intake")
router.register(r"plan", nutrition_api.PlanViewSet, basename="nutrition-plan")

grimoire_router = SimpleRouter()
grimoire_router.register(r"reagents", grimoire_api.ReagentViewSet, basename="grimoire-reagent")
grimoire_router.register(r"spells", grimoire_api.SpellViewSet, basename="grimoire-spell")
grimoire_router.register(r"tags", grimoire_api.TagViewSet, basename="grimoire-tag")
grimoire_router.register(r"castings", grimoire_api.CastingViewSet, basename="grimoire-casting")

api_urlpatterns = [
    path("catalog/", api.CatalogView.as_view(), name="forge-catalog"),
    path("equipment/", api.EquipmentProfileView.as_view(), name="forge-equipment"),
    path("assessment/", api.AssessmentView.as_view(), name="forge-assessment"),
    path("assessment/retake/", api.RetakeAssessmentView.as_view(), name="forge-retake"),
    path(
        "assessment/history/",
        api.AssessmentHistoryView.as_view(),
        name="forge-assessment-history",
    ),
    path(
        "assessment/reminder/dismiss/",
        api.RetestReminderDismissView.as_view(),
        name="forge-retest-dismiss",
    ),
    path("program/", api.ProgramView.as_view(), name="forge-program"),
    path(
        "progression/<uuid:presc_uuid>/<str:action>/",
        api.ProgressionActionView.as_view(),
        name="forge-progression",
    ),
    path(
        "prescription/<uuid:presc_uuid>/set-rung/",
        api.SetRungView.as_view(),
        name="forge-set-rung",
    ),
    path("rungs/", api.RungOverviewView.as_view(), name="forge-rungs"),
    path("today/", api.TodayView.as_view(), name="forge-today"),
    path(
        "today/swap-candidates/",
        api.SwapCandidatesView.as_view(),
        name="forge-swap-candidates",
    ),
    path("norms/", api.NormsView.as_view(), name="forge-norms"),
    path("progress/", api.ProgressView.as_view(), name="forge-progress"),
    path(
        "grimoire/preferences/",
        grimoire_api.PreferencesView.as_view(),
        name="grimoire-preferences",
    ),
    path("grimoire/", include(grimoire_router.urls)),
    path("legal/", nutrition_api.LegalView.as_view(), name="legal"),
    path("legal/accept/", nutrition_api.LegalAcceptView.as_view(), name="legal-accept"),
    path("legal/withdraw/", nutrition_api.LegalWithdrawView.as_view(), name="legal-withdraw"),
    path("me/", nutrition_api.MeView.as_view(), name="nutrition-me"),
    path("goals/", nutrition_api.GoalsView.as_view(), name="nutrition-goals"),
    path("goals/history/", nutrition_api.GoalHistoryView.as_view(), name="nutrition-goal-history"),
    path("adherence/", nutrition_api.AdherenceView.as_view(), name="nutrition-adherence"),
    path("rolling/", nutrition_api.RollingView.as_view(), name="nutrition-rolling"),
    path("mcp/connections/", oauth_views.McpConnectionsView.as_view(), name="mcp-connections"),
    path("mcp/connect-clicked/", oauth_views.McpConnectClickView.as_view(), name="mcp-connect-clicked"),
    path(
        "mcp/connections/<uuid:uuid>/",
        oauth_views.McpConnectionDetailView.as_view(),
        name="mcp-connection",
    ),
    path("", include(router.urls)),
]

urlpatterns = [
    path('', views.landing_view, name='landing'),
    path('terms/', TemplateView.as_view(template_name='the_cauldron/legal/terms.html'), name='terms'),
    path('privacy/', TemplateView.as_view(template_name='the_cauldron/legal/privacy.html'), name='privacy'),
    path('accept/', views.legal_accept_view, name='legal-accept'),
    path('profile/', views.profile_view, name='profile'),
    path('journal/', views.journal_view, name='journal'),
    # The MCP endpoint (#89); a browser GET renders the info page. OAuth routes +
    # /.well-known/ live in oauth_urls.py, included at the site root by arcano.
    path('mcp/', McpView.as_view(), name='mcp'),
    # Without the slash too: APPEND_SLASH's 301 turns a client's POST into a
    # GET without body or token, so a URL pasted without it got no tools.
    path('mcp', McpView.as_view(), name='mcp-no-slash'),
    path('forge/', views.forge_view, name='forge'),
    path('forge/<slug:section>/', views.forge_view, name='forge-section'),
    path('grimoire/', views.grimoire_view, name='grimoire'),
    path('grimoire/spell/new/', views.grimoire_edit_view, name='grimoire-spell-new'),
    path('grimoire/reagent/new/', views.grimoire_reagent_new_view, name='grimoire-reagent-new'),
    path('grimoire/reagent/<uuid:reagent_uuid>/', views.grimoire_reagent_view, name='grimoire-reagent'),
    path(
        'grimoire/reagent/<uuid:reagent_uuid>/edit/',
        views.grimoire_reagent_new_view,
        name='grimoire-reagent-edit',
    ),
    path('grimoire/spell/<uuid:spell_uuid>/', views.grimoire_spell_view, name='grimoire-spell'),
    path('grimoire/spell/<uuid:spell_uuid>/edit/', views.grimoire_edit_view, name='grimoire-spell-edit'),
    path('grimoire/spell/<uuid:spell_uuid>/cast/', views.grimoire_cast_view, name='grimoire-cast'),
    path(
        'grimoire/casting/<uuid:casting_uuid>/edit/',
        views.grimoire_casting_edit_view,
        name='grimoire-casting-edit',
    ),
    path(
        'login/',
        auth_views.LoginView.as_view(
            template_name='the_cauldron/login.html',
            redirect_authenticated_user=True,
        ),
        name='login',
    ),
    path(
        'logout/',
        auth_views.LogoutView.as_view(next_page='the_cauldron:landing'),
        name='logout',
    ),
    path('api/', include((api_urlpatterns, 'forge-api'))),
]
