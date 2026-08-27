from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/",        admin.site.urls),
    path("health/",       health,                        name="health"),
    path("api/accounts/", include("apps.accounts.urls")),
    path("api/angel/",    include("apps.accounts.urls")),   # backward compat
    path("api/admin/",    include("apps.admin_api.urls")),
]
