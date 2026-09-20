import asyncio
import re
from urllib.parse import quote

from src.providers._http import fetch_json, fetch_text
from src.providers._match import (
    attr, build_titles, episode_meta, expected_count,
    find_top_slugs, get_prequel_offset, select_series, strip_tags,
)
from src.providers._media import build_ctx

NAME = "anikin"
BASE = "https://anikin.cc"

async def search(query: str) -> list:
    html = await fetch_text(f"{BASE}/search?q={quote(query)}")
    results = []
    for m in re.finditer(
            r'<div\b[^>]*class=["\']fim-title["\'][^>]*>[\s\S]*?<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>([\s\S]*?)</a>',
            html, re.IGNORECASE):
        href = m.group(1)
        sm = re.search(r"/anime/([^/?#]+)", href)
        if not sm:
            continue
        text = strip_tags(m.group(2))
        results.append({"slug": sm.group(1), "text": text})
    return results

async def scrape_series(slug: str) -> list:
    html = await fetch_text(f"{BASE}/anime/{slug}")
    episodes = []
    # Simplified parsing based on common patterns
    for m in re.finditer(
            r'<a\b[^>]*href=["\']([^"\']+)/ep-(\d+)["\'][^>]*>[\s\S]*?</a>',
            html, re.IGNORECASE):
        number = int(m.group(2))
        episodes.append({
            "number": number,
            "title": f"Episode {number}",
            "epSlug": f"{m.group(1)}/ep-{number}",
            "hasSub": True,
            "hasDub": False,
        })
    episodes.sort(key=lambda e: e["number"])
    seen, out = set(), []
    for e in episodes:
        if e["number"] not in seen:
            seen.add(e["number"])
            out.append(e)
    return out

async def watch(anilist_id: int, audio: str, ep: int, ctx: dict | None = None) -> list:
    # Requires extraction logic for the video streams.
    # Placeholder to be filled based on network inspection of anikin.cc
    return []

async def get_episodes(anilist_id: int, ctx: dict | None = None) -> dict:
    ctx = ctx or await build_ctx(anilist_id)
    media = ctx["media"]
    titles = build_titles(media, ctx.get("anizip"))
    candidates = await find_top_slugs(titles, search)
    if not candidates:
        raise RuntimeError(f"Anikin match not found for AniList {anilist_id}")

    selected = candidates[0] # Simplification
    episodes = await scrape_series(selected["slug"])
    expected = expected_count(media, ctx.get("anizip"))
    return {
        "meta": {
            "id": selected["slug"],
            "title": selected["title"],
            "source": NAME,
        },
        "episodes": {"sub": [e for e in episodes if e["hasSub"]], "dub": []},
    }
