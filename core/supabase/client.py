"""
Single Supabase access point (service-role; bypasses RLS — server-side only).

  get_client()                   lazy, cached client built from env
  verify_supabase_access_token() validate an incoming user JWT -> user_id
  save_task_result()             write a celery_tasks row
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from django.conf import settings
from supabase import AuthApiError, Client, create_client


class SupabaseAuthError(Exception):
    pass


@lru_cache(maxsize=1)
def get_client() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not configured")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)


def verify_supabase_access_token(access_token: str) -> str:
    """Validate a Supabase user JWT and return the user id.

    Verification happens server-side via Supabase's own auth service
    (`auth.get_user`), not by decoding the JWT locally. This works
    regardless of whether the project signs tokens with HS256 or ES256,
    and there is no path that skips signature verification -- a bad or
    expired token raises, it is never silently accepted.
    """
    if not access_token:
        raise SupabaseAuthError("Access token missing")

    try:
        result = get_client().auth.get_user(access_token)
    except AuthApiError as exc:
        raise SupabaseAuthError(f"Invalid Supabase token: {exc}")
    except Exception as exc:
        raise SupabaseAuthError(f"Token verification failed: {exc}")

    if not result or not result.user or not result.user.id:
        raise SupabaseAuthError("Invalid token: no user")

    return result.user.id


def save_task_result(task_name: str, status: str, result=None) -> None:
    get_client().table("celery_tasks").insert(
        {
            "task_name": task_name,
            "status": status,
            "result": result,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    ).execute()
