from django.urls import path
from . import views

urlpatterns = [
    path("trigger/stock-hedge/",   views.trigger_stock_hedge,       name="trigger-stock-hedge"),
    path("trigger/opening-bell/",  views.trigger_opening_bell,      name="trigger-opening-bell"),
    path("trigger/jackpot/",       views.trigger_jackpot,           name="trigger-jackpot"),
    path("trigger/test/",          views.trigger_test,              name="trigger-test"),
    path("overnight-trades/",      views.active_overnight_trades,   name="overnight-trades"),
]
