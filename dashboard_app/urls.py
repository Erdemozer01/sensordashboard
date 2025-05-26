from django.urls import path
from . import views

app_name = 'dashboard_app' # Namespace için

urlpatterns = [
    path('', views.dashboard_display_view, name='realtime_dashboard'),
]