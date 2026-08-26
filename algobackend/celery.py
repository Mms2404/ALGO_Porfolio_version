"""Celery app + Beat schedule.

NOTE (portfolio version): strategy names and exact schedule times below
are illustrative placeholders. The real production system trades live
index options on a real schedule; those specifics are the actual trading
edge and aren't published here. Everything else -- the scheduling
architecture, the caching/preload pattern, the django.setup() handling,
the manual-trigger examples -- reflects the real, deployed system as-is.
"""

import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "algobackend.settings")

import django
try:
    django.setup()
except Exception as _setup_exc:
    # This module gets imported by BOTH the Celery worker (via
    # algobackend/__init__.py -> celery.py) AND the gunicorn web process
    # (gunicorn importing algobackend.wsgi also runs __init__.py first,
    # which pulls this file in too). Only the worker strictly depends on
    # this call succeeding here -- nothing else configures Django for it.
    # The web process gets its own proper django.setup() from wsgi.py
    # right after this, so don't crash the whole gunicorn boot if this
    # particular call fails in that context.
    print(f"celery.py: django.setup() deferred here: {_setup_exc}", file=sys.stderr)

app = Celery("algobackend")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ---------------------------------------------------------------------------
# Beat schedule  (all times IST — CELERY_TIMEZONE = "Asia/Kolkata" in settings)
# ---------------------------------------------------------------------------
app.conf.beat_schedule = {
    # Pre-open token refresh -- logs every user's broker session in fresh
    # each morning, so no strategy has to deal with a stale/expired token
    # mid-run.
    "refresh-broker-tokens": {
        "task": "apps.scheduling.tasks.refresh_all_tokens",
        "schedule": crontab(hour=7, minute=0, day_of_week="1-5"),
    },

    # Pre-warm the instrument master file cache -- fires N copies aiming
    # to warm every Celery worker process's in-memory cache before market
    # open, not just whichever ONE process a single task would land on.
    # See apps/marketdata/instruments.py fetch_scrip_master().
    "warm-instrument-cache": {
        "task": "apps.scheduling.tasks.warm_scrip_master_task",
        "schedule": crontab(hour=7, minute=5, day_of_week="1-5"),
        "kwargs": {"worker_count": 5},  # match your --concurrency value
    },

    # Pick which index to trade today (whichever has the nearer expiry) --
    # shared by every index-option strategy, so they all trade the same
    # pick instead of each deciding independently. Must run after the
    # instrument cache is warm.
    "select-daily-instrument": {
        "task": "apps.scheduling.tasks.select_daily_instrument_task",
        "schedule": crontab(hour=9, minute=11, day_of_week="1-5"),
    },

    # Strategy A -- illustrative placeholder for a real scheduled strategy.
    # "mode": "LIVE" or "DRY" is the live/dry switch, set per-strategy
    # right here, not read from a global env var.
    "run-strategy-a": {
        "task": "apps.scheduling.tasks.run_strategy_a_task",
        "schedule": crontab(hour=9, minute=15, day_of_week="1-5"),
        "kwargs": {"mode": "DRY"},
    },

    # Strategy B -- illustrative placeholder.
    "run-strategy-b": {
        "task": "apps.scheduling.tasks.run_strategy_b_task",
        "schedule": crontab(hour=9, minute=20, day_of_week="1-5"),
        "kwargs": {"mode": "DRY"},
    },

    # # Strategy C -- illustrative placeholder, disabled by default.
    # "run-strategy-c": {
    #     "task": "apps.scheduling.tasks.run_strategy_c_task",
    #     "schedule": crontab(hour=14, minute=30, day_of_week="1-5"),
    #     "kwargs": {"mode": "DRY"},
    # },

    # Resume any overnight positions after a server restart.
    # "resume-overnight-positions": {
    #     "task": "apps.scheduling.tasks.resume_overnight_trades_task",
    #     "schedule": crontab(hour=9, minute=14, day_of_week="1-5"),
    # },

    # Periodic P&L snapshot for any open overnight positions.
    "snapshot-overnight-pnl": {
        "task": "apps.scheduling.tasks.snapshot_overnight_pnl_task",
        "schedule": crontab(minute="*/15", hour="9-15", day_of_week="1-5"),
    },
}

# To manually trigger a task right now (no Beat entry, no restart needed):
#     celery -A algobackend call apps.scheduling.tasks.run_strategy_a_task

# IMPORTANT: delete the celerybeat-schedule file any time you change a
# schedule above (or restart mid-session) -- Beat persists "last run"
# state to that file, and stale state can cause a task to fire again
# unexpectedly against an old schedule. This bit us once in production
# (see BUG_LOG.md #22) -- make it a habit on every restart, not just the
# one where you changed a schedule.
