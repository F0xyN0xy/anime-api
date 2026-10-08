"""
H-anime provider — hstream.moe + hanime.tv
==========================================

Why this exists: generic providers either don't index H content at all or
return wrong-anime streams for H IDs. These two sites are H-specific with
clean slug-based URLs:
  hstream.moe/hentai/{slug}
  hanime.tv/videos/hentai/{slug}

Strategy: slugify the AniList title variants, probe both sites, scrape the
HTML for video sources (m3u8/mp4/embed), and return them as streams.

This provider is ONLY used for adult content — the frontend resolver
checks `isAdult` from AniList before including it in the race.
"""

import asyncio
import re
from urllib.parse import quote

from src.providers import _cache
from src.providers._http import fetch_text
from src.providers._media import build_ctx

NAME = "hanime"
BASE_HSTREAM = "https://hstream.moe"
BASE_HANIME = "https://hanime.tv"


def _slugify(title: str) -> str:
    """Slugify a title for hstream/hanime URL patterns."""
    return (
        title.lower()
        .replace("'", "")
        .replace("’", "")
        .replace("&", " and ")
        .replace(":", "")
        .replace("!", "")
        .replace("?", "")
        .replace(".", "")
        .replace(",", "")
        .replace("~", "")
        .replace(" ", "-")
        .replace("--", "-")
        .strip("-")
    )


def _build_slugs(media: dict) -> list[str]:
    """Generate slug candidates from all title variants."""
    titles = []
    t = media.get("title") or {}
    for key in ("english", "romaji", "native"):
        v = t.get(key)
        if v:
            titles.append(v)
    # Also try without season markers
    for syn in media.get("synonyms") or []:
        if syn:
            titles.append(syn)

    seen = set()
    slugs = []
    for title in titles:
        s = _slugify(title)
        if s and s not in seen:
            seen.add(s)
            slugs.append(s)
        # Also try stripping common suffixes
        for suffix in (" the animation", " ova", " special"):
            if s.endswith(suffix.replace(" ", "-")):
                stripped = s[: -len(suffix.replace(" ", "-"))]
                if stripped and stripped not in seen:
                    seen.add(stripped)
                    slugs.append(stripped)
    return slugs


def _extract_streams(html: str, base: str) -> list:
    """Extract video sources from an hstream.moe or hanime.tv page."""
    streams = []

    # 1. Direct <source> or <video src> tags
    for m in re.finditer(
        r'<(?:source|video)[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE
    ):
        url = m.group(1)
        if url.startswith("/"):
            url = f"{base}{url}"
        if ".m3u8" in url:
            streams.append({
                "url": url,
                "type": "hls",
                "quality": "auto",
                "audio": "sub",
                "server": NAME,
                "referer": base,
            })
        elif re.search(r"\.(mp4|webm|mkv)", url, re.IGNORECASE):
            streams.append({
                "url": url,
                "type": "mp4",
                "quality": "auto",
                "audio": "sub",
                "server": NAME,
                "referer": base,
            })

    # 2. JSON-LD structured data
    for m in re.finditer(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
        html, re.IGNORECASE,
    ):
        try:
            import json as _json
            data = _json.loads(m.group(1))
            videos = data if isinstance(data, list) else [data]
            for v in videos:
                url = v.get("contentUrl") or v.get("embedUrl") or v.get("url")
                if isinstance(url, str):
                    if url.startswith("/"):
                        url = f"{base}{url}"
                    if ".m3u8" in url:
                        streams.append({
                            "url": url, "type": "hls", "quality": "auto",
                            "audio": "sub", "server": NAME, "referer": base,
                        })
                    elif re.search(r"\.(mp4|webm)", url, re.IGNORECASE) or "embed" in url:
                        streams.append({
                            "url": url, "type": "mp4", "quality": "auto",
                            "audio": "sub", "server": NAME, "referer": base,
                        })
        except Exception:
            pass

    # 3. Common embed patterns (iframe src)
    for m in re.finditer(
        r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE
    ):
        url = m.group(1)
        if url.startswith("/"):
            url = f"{base}{url}"
        if any(k in url for k in ("player", "embed", "stream")):
            streams.append({
                "url": url,
                "type": "embed",
                "quality": "auto",
                "audio": "sub",
                "server": f"{NAME}-embed",
                "referer": base,
            })

    # 4. Look for m3u8/mp4 URLs in script tags (common in JW Player setups)
    for m in re.finditer(
        r'["\'](https?://[^"\']*?\.m3u8[^"\']*)["\']', html
    ):
        url = m.group(1)
        if url not in [s["url"] for s in streams]:
            streams.append({
                "url": url, "type": "hls", "quality": "auto",
                "audio": "sub", "server": NAME, "referer": base,
            })
    for m in re.finditer(
        r'["\'](https?://[^"\']*?\.mp4[^"\']*)["\']', html
    ):
        url = m.group(1)
        if url not in [s["url"] for s in streams]:
            streams.append({
                "url": url, "type": "mp4", "quality": "auto",
                "audio": "sub", "server": NAME, "referer": base,
            })

    return streams


def _extract_subtitles(html: str, base: str) -> list:
    """Extract subtitle tracks from the page."""
    subs = []
    for m in re.finditer(
        r'<track[^>]+src=["\']([^"\']+)["\'][^>]*(?:label|srclang)=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    ):
        url = m.group(1)
        if url.startswith("/"):
            url = f"{base}{url}"
        subs.append({"url": url, "lang": m.group(2)})
    # Also try reversed attribute order
    for m in re.finditer(
        r'<track[^>]*(?:label|srclang)=["\']([^"\']+)["\'][^>]*src=["\']([^"\']+)["\']',
        html, re.IGNORECASE,
    ):
        url = m.group(2)
        if url.startswith("/"):
            url = f"{base}{url}"
        entry = {"url": url, "lang": m.group(1)}
        if entry not in subs:
            subs.append(entry)
    return subs


async def _probe_page(url: str) -> str | None:
    """Check if a page exists and looks like a valid video page."""
    try:
        html = await fetch_text(url)
        if not html:
            return None
        # Must look like a video page
        if any(k in html for k in ("<video", "player", "embed", ".m3u8", ".mp4")):
            return html
        return None
    except Exception:
        return None


async def _find_page(media: dict, episode: int) -> tuple[str, str] | None:
    """Find the correct page URL on hstream or hanime. Returns (base, html)."""
    slugs = _build_slugs(media)
    if not slugs:
        return None

    # For multi-episode series, try episode suffixes
    ep_suffixes = [""]
    if episode > 1:
        ep_suffixes = [f"-{episode}", f"-{str(episode).zfill(2)}", ""]

    for slug in slugs:
        for suffix in ep_suffixes:
            targets = [
                (BASE_HSTREAM, f"{BASE_HSTREAM}/hentai/{slug}{suffix}"),
                (BASE_HANIME, f"{BASE_HANIME}/videos/hentai/{slug}{suffix}"),
            ]
            for base, url in targets:
                html = await _probe_page(url)
                if html:
                    return (base, html)
    return None


async def get_episodes(anilist_id: int, ctx: dict | None = None) -> dict:
    """Return episode list for an H-anime.

    H-anime sites typically list all episodes on a single page, so we
    return a synthetic episode list based on the AniList count.
    """
    ctx = ctx or await build_ctx(anilist_id)
    media = ctx["media"]
    expected = media.get("episodes") or 1

    # Try to find the page to confirm it exists
    found = await _find_page(media, 1)
    if not found:
        raise RuntimeError(f"Hanime: no page found for AniList {anilist_id}")

    # Build synthetic episode list
    sub = []
    for i in range(1, expected + 1):
        sub.append({
            "id": f"watch/{NAME}/{anilist_id}/sub/{NAME}-{i}",
            "number": i,
            "title": f"Episode {i}",
            "audio": "sub",
        })

    return {
        "meta": {
            "id": NAME,
            "title": (media.get("title") or {}).get("english")
                     or (media.get("title") or {}).get("romaji")
                     or "Unknown",
            "source": NAME,
            "matchScore": 1.0,
            "numbering": "absolute",
            "episodeOffset": 0,
        },
        "episodes": {"sub": sub, "dub": []},
    }


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict | None = None) -> list:
    """Extract streams for an H-anime episode."""
    ctx = ctx or await build_ctx(anilist_id)
    media = ctx["media"]

    found = await _find_page(media, ep)
    if not found:
        raise RuntimeError(f"Hanime: episode {ep} not found for AniList {anilist_id}")

    base, html = found
    streams = _extract_streams(html, base)
    if not streams:
        raise RuntimeError(f"Hanime: no streams extracted for episode {ep}")

    # Deduplicate by URL
    seen = set()
    unique = []
    for s in streams:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)

    return unique