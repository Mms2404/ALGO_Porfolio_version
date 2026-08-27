from django.urls import path
from . import views

urlpatterns = [
    path("validate/", views.validate_account, name="angel-validate"),
    path("balance/",  views.get_balance,       name="angel-balance"),
]
