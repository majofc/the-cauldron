from django.contrib.auth import views as auth_views
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import api, views

app_name = "the_cauldron"

router = DefaultRouter()
router.register(r"patterns", api.PatternViewSet, basename="forge-pattern")
router.register(r"exercises", api.ExerciseViewSet, basename="forge-exercise")
router.register(r"sessions", api.SessionViewSet, basename="forge-session")

api_urlpatterns = [
    path("catalog/", api.CatalogView.as_view(), name="forge-catalog"),
    path("equipment/", api.EquipmentProfileView.as_view(), name="forge-equipment"),
    path("assessment/", api.AssessmentView.as_view(), name="forge-assessment"),
    path("assessment/retake/", api.RetakeAssessmentView.as_view(), name="forge-retake"),
    path("program/", api.ProgramView.as_view(), name="forge-program"),
    path(
        "progression/<uuid:presc_uuid>/<str:action>/",
        api.ProgressionActionView.as_view(),
        name="forge-progression",
    ),
    path("today/", api.TodayView.as_view(), name="forge-today"),
    path("norms/", api.NormsView.as_view(), name="forge-norms"),
    path("progress/", api.ProgressView.as_view(), name="forge-progress"),
    path("", include(router.urls)),
]

urlpatterns = [
    path('', views.landing_view, name='landing'),
    path('forge/', views.forge_view, name='forge'),
    path('forge/<slug:section>/', views.forge_view, name='forge-section'),
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
