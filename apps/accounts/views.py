from __future__ import annotations
from datetime import datetime
import pytz

import requests
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.angel.auth import login_to_angel
from core.angel.headers import get_default_headers
from core.crypto.cipher import decrypt_token, decrypt_with_private_key, encrypt_token
from core.responses import error_response, success_response
from core.supabase.client import get_client, verify_supabase_access_token

RMS_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/user/v1/getRMS"


def _get_user_id(request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return verify_supabase_access_token(auth.replace("Bearer ", "").strip())


def _rsa_decrypt(value) -> str:
    """Decode RSA-encrypted field from DB (may arrive as memoryview, bytes, or str)."""
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    return decrypt_with_private_key(value)


@api_view(["POST"])
def validate_account(request):
    try:
        user_id = _get_user_id(request)
        if not user_id:
            return Response(error_response("Missing or invalid Authorization header", "AUTH_MISSING"), status=401)

        sb = get_client()
        row = sb.table("angel_accounts").select(
            "client_id, pin, totp_secret, api_key"
        ).eq("user_id", user_id).single().execute().data

        if not row:
            return Response(error_response("Angel account not found", "ACCOUNT_NOT_FOUND"), status=404)

        client_id   = row["client_id"]
        pin         = _rsa_decrypt(row["pin"])
        totp_secret = _rsa_decrypt(row["totp_secret"])
        api_key     = _rsa_decrypt(row["api_key"])

        login_resp = login_to_angel(
            client_code=client_id,
            password=pin,
            totp_secret=totp_secret,
            api_key=api_key,
        )

        if not login_resp.get("status"):
            return Response(
                error_response(login_resp.get("message", "Angel login failed"), "ANGEL_LOGIN_FAILED"),
                status=400,
            )

        tokens = login_resp["data"]
        sb.table("angel_accounts").update({
            "jwt_token":     encrypt_token(tokens["jwt_token"]),
            "feed_token":    encrypt_token(tokens["feed_token"]),
            "refresh_token": encrypt_token(tokens["refresh_token"]),
            "is_verified":   True,
             "updated_at":    datetime.now(pytz.utc).isoformat(),
        }).eq("user_id", user_id).execute()

        return Response(success_response(message="Angel account verified successfully"))

    except Exception as exc:
        return Response(error_response(str(exc), "AB_INTERNAL_ERROR"), status=500)


@api_view(["GET"])
def get_balance(request):
    try:
        user_id = _get_user_id(request)
        if not user_id:
            return Response(error_response("Missing or invalid Authorization header", "AUTH_MISSING"), status=401)

        sb = get_client()
        row = sb.table("angel_accounts").select(
            "api_key, jwt_token, is_verified"
        ).eq("user_id", user_id).single().execute().data

        if not row:
            return Response(error_response("Angel account not found", "ACCOUNT_NOT_FOUND"), status=404)

        if not row.get("is_verified"):
            return Response(error_response("Angel account not verified", "NOT_VERIFIED"), status=400)

        api_key   = _rsa_decrypt(row["api_key"])
        jwt_token = decrypt_token(row["jwt_token"])

        headers = get_default_headers(api_key=api_key, include_jwt=jwt_token)
        resp = requests.get(RMS_URL, headers=headers, timeout=15)

        if not resp.text.strip():
            return Response(error_response("Empty response from Angel API", "EMPTY_RESPONSE"), status=502)

        data = resp.json()
        if not data.get("status"):
            return Response(
                error_response(data.get("message"), data.get("errorcode")),
                status=400,
            )

        return Response(success_response(data=data.get("data", {}), message="Balance fetched successfully"))

    except Exception as exc:
        return Response(error_response(str(exc), "AB_INTERNAL_ERROR"), status=500)
