"""
H-anime provider — hentaiocean.com (primary) + hstream.moe + hanime.tv
=======================================================================

Why this exists: generic providers either don't index H content at all or
return wrong-anime streams for H IDs. These sites are H-specific.

hentaiocean.com has a proper API:
  Fetch:  https://hentaiocean.com/api?action=hentai&slug={slug}
  Embed:  https://hentaiocean.com/embed/{slug}
  RSS:    https://hentaiocean.com/rss.xml (for reference)

hstream.moe and hanime.tv use slug-based URLs:
  hstream.moe/hentai/{slug}
  hanime.tv/videos/hentai/{slug}

Strategy: slugify the AniList title variants, try hentaiocean API first
(proper JSON response), fall back to hstream/hanime HTML scraping.

This provider is ONLY used for adult content — the frontend resolver
checks `isAdult` from AniList before including it in the race.
"""

import json
import re

from src.providers._http import fetch_text
from src.providers._media import build_ctx

NAME = "hanime"
BASE_HENTAIOCEAN = "https://hentaiocean.com"
BASE_HSTREAM = "https://hstream.moe"
BASE_HANIME = "https://hanime.tv"


def _slugify(title: str) -> str:
    """Slugify a title for URL patterns."""
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


def _build_slugs(media: dict, episode: int) -> list:
    """Generate slug candidates from all title variants.

    Sites index by title slug, not AniList ID. The AniList title often
    differs from what the site uses. We generate many candidates:
    - Full title slug
    - Without "the animation" / "ova" / "special" suffixes
    - With episode suffix (for multi-episode series)
    """
    titles = []
    t = media.get("title") or {}
    for key in ("english", "romaji", "native"):
        v = t.get(key)
        if v:
            titles.append(v)
    for syn in media.get("synonyms") or []:
        if syn:
            titles.append(syn)

    seen = set()
    slugs = []
    for title in titles:
        s = _slugify(title)
        if not s:
            continue
        print(f"[hanime] slug candidate from {title!r}: {s}")

        # Base slug (no episode suffix)
        if s not in seen:
            seen.add(s)
            slugs.append(s)

        # Strip common suffixes that sites often omit
        for suffix in ("-the-animation", "-ova", "-special", "-season-1", "-season-2", "-season-3"):
            if s.endswith(suffix):
                stripped = s[: -len(suffix)]
                if stripped and stripped not in seen:
                    seen.add(stripped)
                    slugs.append(stripped)

        # Try without trailing numbers (e.g. "kokuhaku-4" -> "kokuhaku")
        base = re.sub(r"-\d+$", "", s)
        if base and base != s and base not in seen:
            seen.add(base)
            slugs.append(base)

        # For multi-episode series, try with episode suffix
        if episode > 1:
            ep_slug = f"{s}-{episode}"
            if ep_slug not in seen:
                seen.add(ep_slug)
                slugs.append(ep_slug)
            # Also try zero-padded
            ep_slug2 = f"{s}-{str(episode).zfill(2)}"
            if ep_slug2 not in seen:
                seen.add(ep_slug2)
                slugs.append(ep_slug2)

    print(f"[hanime] generated {len(slugs)} slug candidates: {slugs[:5]}...")
    return slugs


async def _fetch_json(url: str) -> dict | None:
    """Fetch a URL and return parsed JSON, or None on failure."""
    try:
        text = await fetch_text(url)
        if not text:
            print(f"[hanime] empty response from {url}")
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"[hanime] JSON decode error from {url}: {e}")
            print(f"[hanime] response preview: {text[:200]}")
            return None
    except Exception as e:
        print(f"[hanime] fetch error from {url}: {e}")
        return None


async def _try_hentaiocean(slug: str) -> dict | None:
    """Try hentaiocean.com API for a slug. Returns info dict or None."""
    url = f"{BASE_HENTAIOCEAN}/api?action=hentai&slug={slug}"
    print(f"[hanime] trying hentaiocean: {url}")
    data = await _fetch_json(url)
    if data:
        print(f"[hanime] hentaiocean response keys: {list(data.keys())}")
        if data.get("info") and len(data["info"]) > 0:
            print(f"[hanime] hentaiocean found: {data['info'][0].get('title', 'unknown')}")
            return data["info"][0]
        else:
            print(f"[hanime] hentaiocean no info for slug={slug}")
    return None


def _hentaiocean_streams(info: dict, slug: str) -> list:
    """Build stream entries from hentaiocean info."""
    streams = []
    # The embed URL is the playable stream
    embed_url = f"{BASE_HENTAIOCEAN}/embed/{slug}"
    streams.append({
        "url": embed_url,
        "type": "embed",
        "quality": "auto",
        "audio": "sub",
        "server": NAME,
        "referer": BASE_HENTAIOCEAN,
    })
    return streams


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
            data = json.loads(m.group(1))
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

    # 4. Look for m3u8/mp4 URLs in script tags
    for m in re.finditer(r'["\'](https?://[^"\']*?\.m3u8[^"\']*)["\']', html):
        url = m.group(1)
        if url not in [s["url"] for s in streams]:
            streams.append({
                "url": url, "type": "hls", "quality": "auto",
                "audio": "sub", "server": NAME, "referer": base,
            })
    for m in re.finditer(r'["\'](https?://[^"\']*?\.mp4[^"\']*)["\']', html):
        url = m.group(1)
        if url not in [s["url"] for s in streams]:
            streams.append({
                "url": url, "type": "mp4", "quality": "auto",
                "audio": "sub", "server": NAME, "referer": base,
            })

    return streams


async def _probe_page(url: str) -> str | None:
    """Check if a page exists and looks like a valid video page."""
    try:
        html = await fetch_text(url)
        if not html:
            print(f"[hanime] empty page: {url}")
            return None
        if any(k in html for k in ("<video", "player", "embed", ".m3u8", ".mp4")):
            print(f"[hanime] valid page found: {url}")
            return html
        print(f"[hanime] page exists but no video markers: {url}")
        return None
    except Exception as e:
        print(f"[hanime] probe error {url}: {e}")
        return None


async def _find_hentaiocean(media: dict, episode: int) -> tuple[str, list] | None:
    """Try hentaiocean.com API. Returns (slug, streams) or None."""
    slugs = _build_slugs(media, episode)
    for slug in slugs:
        info = await _try_hentaiocean(slug)
        if info:
            streams = _hentaiocean_streams(info, slug)
            if streams:
                return (slug, streams)
    return None


async def _find_html_page(media: dict, episode: int) -> tuple[str, list] | None:
    """Try hstream.moe and hanime.tv. Returns (base, streams) or None."""
    slugs = _build_slugs(media, episode)
    for slug in slugs:
        targets = [
            (BASE_HSTREAM, f"{BASE_HSTREAM}/hentai/{slug}"),
            (BASE_HANIME, f"{BASE_HANIME}/videos/hentai/{slug}"),
        ]
        for base, url in targets:
            html = await _probe_page(url)
            if html:
                streams = _extract_streams(html, base)
                if streams:
                    return (base, streams)
    return None


async def get_episodes(anilist_id: int, ctx: dict | None = None) -> dict:
    """Return episode list for an H-anime."""
    if ctx is None:
        ctx = await build_ctx(anilist_id)

    media = ctx["media"]
    expected = media.get("episodes") or 1

    # Try to find the anime on hentaiocean to confirm it exists
    found = await _find_hentaiocean(media, 1)
    if not found:
        # Try HTML sites as fallback
        found = await _find_html_page(media, 1)
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
    if ctx is None:
        ctx = await build_ctx(anilist_id)

    media = ctx["media"]
    title = (media.get("title") or {}).get("english") or (media.get("title") or {}).get("romaji") or "unknown"
    print(f"[hanime] watch: anilist_id={anilist_id} title={title!r} ep={ep}")

    # Try hentaiocean first (proper API)
    found = await _find_hentaiocean(media, ep)
    if not found:
        print(f"[hanime] hentaiocean failed, trying hstream/hanime...")
        # Fall back to hstream/hanime HTML scraping
        found = await _find_html_page(media, ep)
    if not found:
        print(f"[hanime] all sources failed for {title!r} ep={ep}")
        raise RuntimeError(f"Hanime: episode {ep} not found for AniList {anilist_id}")

    _, streams = found
    print(f"[hanime] found {len(streams)} streams")

    # Deduplicate by URL
    seen = set()
    unique = []
    for s in streams:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)

    return unique