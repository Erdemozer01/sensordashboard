from django.apps import AppConfig


class DashboardAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard_app'

    def ready(self):
        try:
            import dashboard_app.dash_apps  # Dash uygulamalarını yükle
        except ImportError:
            print("dashboard_app.dash_apps yüklenirken bir sorun oluştu (belki ilk migrate sırasında).")
