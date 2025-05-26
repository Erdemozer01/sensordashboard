from django.shortcuts import render

def dashboard_display_view(request):
    # Dash uygulamasının adı dash_apps.py'de tanımladığımız isim olacak
    # Örnek: app = DjangoDash('RealtimeSensorDashboard', ...)
    context = {'dash_app_name': "RealtimeSensorDashboard"}
    return render(request, 'dashboard_app/dashboard_display.html', context)