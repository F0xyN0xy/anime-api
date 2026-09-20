"""Shared HTTP helpers for the native providers.

Plain httpx with a static User-Agent gets 403'd on sight by the
Cloudflare fronts most provider sites use — that was the source of the
constant "Kuhi 403". Every request here impersonates a real Chrome via
curl_cffi. If sites start fingerprinting again, bump BROWSER.
"""

from typing import Any

from curl_cffi.requests import AsyncSession

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Chrome version curl_cffi impersonates (TLS/JA3/HTTP2 fingerprint).
BROWSER = "chrome124"

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch_text(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> str:
    h = {**DEFAULT_HEADERS, "User-Agent": UA, **(headers or {})}
    async with AsyncSession(impersonate=BROWSER) as session:
        res = await session.get(url, headers=h, timeout=timeout)
        res.raise_for_status()
        return res.text


async def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 20.0,
) -> Any:
    h = {**DEFAULT_HEADERS, "User-Agent": UA, **(headers or {})}
    async with AsyncSession(impersonate=BROWSER) as session:
        res = await session.get(url, headers=h, timeout=timeout)
        res.raise_for_status()
        return res.json()