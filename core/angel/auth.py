"""Angel SmartAPI authentication: login + token refresh.

login_to_angel uses clientcode + PIN (sent as `password`) + TOTP. Credentials
are always passed in per-user (the old single-user constant fallbacks are gone).
"""

import pyotp
import requests

from core.angel.headers import get_default_headers
from core.responses import error_response, success_response

LOGIN_URL = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
REFRESH_URL = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/refreshToken"


def login_to_angel(client_code: str, password: str, totp_secret: str, api_key: str):
    """Returns a success/error envelope; on success data has
    {jwt_token, feed_token, refresh_token}."""
    try:
        totp = pyotp.TOTP(totp_secret).now()
        headers = get_default_headers(api_key)
        body = {
            "clientcode": client_code,
            "password": password,   # this is the user's PIN
            "totp": totp,
            "state": "live",
        }

        resp = requests.post(LOGIN_URL, json=body, headers=headers, timeout=15)

        if not resp.text.strip():
            return error_response(message="Empty response from Angel API")
        try:
            data = resp.json()
        except Exception:
            return error_response(message=f"Non-JSON response: {resp.text[:200]}")

        if not data.get("status"):
            return error_response(
                message=data.get("message"),
                errorcode=data.get("errorcode"),
                data=data.get("data"),
            )

        token_data = data.get("data", {})
        return success_response(
            data={
                "jwt_token": token_data.get("jwtToken"),
                "feed_token": token_data.get("feedToken"),
                "refresh_token": token_data.get("refreshToken"),
            },
            message="Login successful",
        )
    except Exception as exc:
        return error_response(message=str(exc), errorcode="AB_INTERNAL_ERROR")


def refresh_angel_tokens(api_key: str, client_id: str, refresh_token: str):
    """Refresh jwt/feed using the refresh token. Returns (new_jwt, new_feed)."""
    headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}
    payload = {"clientcode": client_id, "refreshToken": refresh_token}

    resp = requests.post(REFRESH_URL, json=payload, headers=headers, timeout=10)
    data = resp.json()

    if not data.get("status"):
        raise Exception(f"Token refresh failed: {data.get('message')}")

    token_data = data.get("data", {})
    return token_data.get("jwtToken"), token_data.get("feedToken")