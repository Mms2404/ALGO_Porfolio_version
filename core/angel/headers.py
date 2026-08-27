"""Angel SmartAPI request headers.

The per-user `api_key` and `jwt` are passed in. The rest are fixed SmartAPI
request-metadata values (not secrets) — previously these lived in the
gitignored constants.py.
"""

USER_TYPE = "USER"
SOURCE_ID = "WEB"
CLIENT_LOCAL_IP = "127.0.0.1"
CLIENT_PUBLIC_IP = "127.0.0.1"
MAC_ADDRESS = "AA:BB:CC:DD:EE:FF"


def get_default_headers(api_key: str, include_jwt: str | None = None) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-PrivateKey": api_key,
        "X-UserType": USER_TYPE,
        "X-SourceID": SOURCE_ID,
        "X-ClientLocalIP": CLIENT_LOCAL_IP,
        "X-ClientPublicIP": CLIENT_PUBLIC_IP,
        "X-MACAddress": MAC_ADDRESS,
    }
    if include_jwt:
        headers["Authorization"] = f"Bearer {include_jwt}"
    return headers