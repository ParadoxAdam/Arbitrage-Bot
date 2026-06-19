"""
eBay OAuth2 client credentials flow.

This gets an application-level access token (no user login needed)
which is sufficient for Browse API read-only access to public listings.

Token is cached in memory and refreshed automatically when expired.
"""
from __future__ import annotations
import base64
import logging
import time
from dataclasses import dataclass
import httpx
from ..config import settings

log = logging.getLogger("ebay.auth")

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SCOPE = "https://api.ebay.com/oauth/api_scope"


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float  # unix timestamp


_token_cache: _CachedToken | None = None


def get_access_token() -> str:
    """
    Get a valid eBay OAuth access token.
    Caches the token and refreshes when it expires.

    Raises ValueError if eBay credentials are not configured.
    Raises RuntimeError if the token request fails.
    """
    global _token_cache

    if not settings.ebay_client_id or not settings.ebay_client_secret:
        raise ValueError(
            "eBay credentials not configured. "
            "Set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET in .env"
        )

    # Return cached token if still valid (with 60s buffer)
    if _token_cache and time.time() < _token_cache.expires_at - 60:
        return _token_cache.access_token

    # Request new token
    credentials = base64.b64encode(
        f"{settings.ebay_client_id}:{settings.ebay_client_secret}".encode()
    ).decode()

    resp = httpx.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope": SCOPE,
        },
        timeout=15,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"eBay OAuth failed ({resp.status_code}): {resp.text}")

    data = resp.json()
    _token_cache = _CachedToken(
        access_token=data["access_token"],
        expires_at=time.time() + data.get("expires_in", 7200),
    )

    log.info("eBay OAuth token acquired (expires in %ss)", data.get("expires_in"))
    return _token_cache.access_token
