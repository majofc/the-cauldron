from django.urls import path
from . import views

app_name = "the_cauldron"

urlpatterns = [
    path('', views.landing_view, name='landing'),
]
