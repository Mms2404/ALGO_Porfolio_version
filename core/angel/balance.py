"""Angel RMS (funds) lookups and per-user credential fetch/decrypt."""

import logging

import requests

from core.angel.headers import get_default_headers
from core.crypto.cipher import decrypt_token, decrypt_with_private_key, encrypt_token
from core.responses import error_response, success_response
from core.supabase.client import SupabaseAuthError, get_client, verify_supabase_access_token

logger = logging.getLogger(__name__)

RMS_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/user/v1/getRMS"

# Angel error codes that mean the JWT is stale
_JWT_EXPIRED_CODES = {"AG8001", "AB1010", "AB1004"}


def fetch_verified_credentials(user_id: str) -> dict:
    """Return {api_key, jwt_token, client_id, pin, totp_secret} (decrypted)."""
    resp = (
        get_client()
        .table("angel_accounts")
        .select("id, client_id, api_key, jwt_token, pin, totp_secret, is_verified")
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    row = resp.data
    if not row:
        raise Exception("Angel account not found")
    if not row.get("is_verified"):
        raise Exception("Angel account not verified. Please validate your account first.")

    def _dec(v):
        if isinstance(v, memoryview): v = v.tobytes()
        if isinstance(v, bytes): v = v.decode("utf-8")
        return v

    api_key = decrypt_with_private_key(_dec(row["api_key"]))

    jwt_enc = row["jwt_token"]
    if not jwt_enc:
        raise Exception("JWT token not found. Please validate your account first.")
    jwt_token = decrypt_token(_dec(jwt_enc))

    pin         = decrypt_with_private_key(_dec(row["pin"]))         if row.get("pin")         else None
    totp_secret = decrypt_with_private_key(_dec(row["totp_secret"])) if row.get("totp_secret") else None

    return {
        "row_id":      row["id"],
        "client_id":   row["client_id"],
        "api_key":     api_key,
        "jwt_token":   jwt_token,
        "pin":         pin,
        "totp_secret": totp_secret,
    }


def _relogin_and_refresh(creds: dict) -> str:
    """Re-login with stored credentials, persist new tokens, return fresh JWT."""
    from core.angel.auth import login_to_angel

    if not creds.get("pin") or not creds.get("totp_secret"):
        raise Exception("Re-login impossible: pin/totp_secret not in DB")

    login_res = login_to_angel(
        client_code=creds["client_id"],
        password=creds["pin"],
        totp_secret=creds["totp_secret"],
        api_key=creds["api_key"],
    )
    if not login_res.get("status"):
        raise Exception(f"Re-login failed: {login_res.get('message')}")

    tokens = login_res["data"]
    get_client().table("angel_accounts").update({
        "jwt_token":     encrypt_token(tokens["jwt_token"]),
        "feed_token":    encrypt_token(tokens["feed_token"]),
        "refresh_token": encrypt_token(tokens["refresh_token"]),
    }).eq("id", creds["row_id"]).execute()

    logger.info("balance: re-login succeeded for %s, tokens refreshed", creds["client_id"])
    return tokens["jwt_token"]


def _call_rms(api_key: str, jwt_token: str) -> dict:
    headers = get_default_headers(api_key=api_key, include_jwt=jwt_token)
    resp = requests.get(RMS_URL, headers=headers, timeout=15)
    if not resp.text.strip():
        raise RuntimeError("Empty response from Angel API")
    return resp.json()


def get_balance_for_user(access_token: str):
    """App endpoint: verify Supabase token -> fetch creds -> RMS funds.
    Auto re-logins once if the stored JWT is expired.
    """
    try:
        user_id = verify_supabase_access_token(access_token)
        creds = fetch_verified_credentials(user_id)

        data = _call_rms(creds["api_key"], creds["jwt_token"])

        # If JWT expired, re-login and retry once
        if not data.get("status") and data.get("errorcode") in _JWT_EXPIRED_CODES:
            logger.warning("balance: JWT expired for user %s, attempting re-login", user_id)
            new_jwt = _relogin_and_refresh(creds)
            data = _call_rms(creds["api_key"], new_jwt)

        if not data.get("status"):
            return error_response(
                message=data.get("message"),
                errorcode=data.get("errorcode"),
                data=data.get("data"),
            )
        return success_response(data=data.get("data", {}), message="Balance fetched successfully")

    except SupabaseAuthError as exc:
        return error_response(message=str(exc), errorcode="AUTH_ERROR")
    except Exception as exc:
        logger.error("get_balance_for_user: %s", exc)
        return error_response(message=str(exc), errorcode="AB_INTERNAL_ERROR")


def get_balance_direct(api_key: str, jwt_token: str) -> float:
    """Return net available balance.

    RAISES on fetch/parse failure (changed from the old silent `return 0.0`),
    so the per-user gate can skip with a reason instead of mistaking a failed
    fetch for a zero balance.
    """
    headers = get_default_headers(api_key=api_key, include_jwt=jwt_token)
    resp = requests.get(RMS_URL, headers=headers, timeout=15)

    if not resp.text.strip():
        raise RuntimeError("Empty response from Angel RMS API")
    data = resp.json()
    if not data.get("status"):
        raise RuntimeError(f"RMS error: {data.get('message')} ({data.get('errorcode')})")

    return float(data.get("data", {}).get("net", 0))