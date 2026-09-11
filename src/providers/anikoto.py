import asyncio
import base64
import json
import re
from urllib.parse import quote, urljoin, urlparse

from src.providers import _cache
from src.providers._http import fetch_json, fetch_text
from src.providers._match import (
    attr, build_titles, decode_entities, episode_meta, expected_count,
    find_top_slugs, get_prequel_offset, select_series, strip_tags,
)
from src.providers._media import build_ctx

NAME = "anikoto"
BASE = "https://anikototv.to"
MAPPER = "https://mapper.nekostream.site/api/mal"
SPOOF_REF = "https://hianimes.re/"

LANG_MAP = {
    "en": "en", "english": "en", "ja": "ja", "japanese": "ja",
    "fr": "fr", "french": "fr", "de": "de", "german": "de",
    "es": "es", "spanish": "es", "pt": "pt", "portuguese": "pt",
}

_SBOX = (
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b,
    0xfe, 0xd7, 0xab, 0x76, 0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0,
    0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0, 0xb7, 0xfd, 0x93, 0x26,
    0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2,
    0xeb, 0x27, 0xb2, 0x75, 0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0,
    0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84, 0x53, 0xd1, 0x00, 0xed,
    0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f,
    0x50, 0x3c, 0x9f, 0xa8, 0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5,
    0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2, 0xcd, 0x0c, 0x13, 0xec,
    0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14,
    0xde, 0x5e, 0x0b, 0xdb, 0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c,
    0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79, 0xe7, 0xc8, 0x37, 0x6d,
    0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f,
    0x4b, 0xbd, 0x8b, 0x8a, 0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e,
    0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e, 0xe1, 0xf8, 0x98, 0x11,
    0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f,
    0xb0, 0x54, 0xbb, 0x16,
)

_INV_SBOX = (
    0x52, 0x09, 0x6a, 0xd5, 0x30, 0x36, 0xa5, 0x38, 0xbf, 0x40, 0xa3, 0x9e,
    0x81, 0xf3, 0xd7, 0xfb, 0x7c, 0xe3, 0x39, 0x82, 0x9b, 0x2f, 0xff, 0x87,
    0x34, 0x8e, 0x43, 0x44, 0xc4, 0xde, 0xe9, 0xcb, 0x54, 0x7b, 0x94, 0x32,
    0xa6, 0xc2, 0x23, 0x3d, 0xee, 0x4c, 0x95, 0x0b, 0x42, 0xfa, 0xc3, 0x4e,
    0x08, 0x2e, 0xa1, 0x66, 0x28, 0xd9, 0x24, 0xb2, 0x76, 0x5b, 0xa2, 0x49,
    0x6d, 0x8b, 0xd1, 0x25, 0x72, 0xf8, 0xf6, 0x64, 0x86, 0x68, 0x98, 0x16,
    0xd4, 0xa4, 0x5c, 0xcc, 0x5d, 0x65, 0xb6, 0x92, 0x6c, 0x70, 0x48, 0x50,
    0xfd, 0xed, 0xb9, 0xda, 0x5e, 0x15, 0x46, 0x57, 0xa7, 0x8d, 0x9d, 0x84,
    0x90, 0xd8, 0xab, 0x00, 0x8c, 0xbc, 0xd3, 0x0a, 0xf7, 0xe4, 0x58, 0x05,
    0xb8, 0xb3, 0x45, 0x06, 0xd0, 0x2c, 0x1e, 0x8f, 0xca, 0x3f, 0x0f, 0x02,
    0xc1, 0xaf, 0xbd, 0x03, 0x01, 0x13, 0x8a, 0x6b, 0x3a, 0x91, 0x11, 0x41,
    0x4f, 0x67, 0xdc, 0xea, 0x97, 0xf2, 0xcf, 0xce, 0xf0, 0xb4, 0xe6, 0x73,
    0x96, 0xac, 0x74, 0x22, 0xe7, 0xad, 0x35, 0x85, 0xe2, 0xf9, 0x37, 0xe8,
    0x1c, 0x75, 0xdf, 0x6e, 0x47, 0xf1, 0x1a, 0x71, 0x1d, 0x29, 0xc5, 0x89,
    0x6f, 0xb7, 0x62, 0x0e, 0xaa, 0x18, 0xbe, 0x1b, 0xfc, 0x56, 0x3e, 0x4b,
    0xc6, 0xd2, 0x79, 0x20, 0x9a, 0xdb, 0xc0, 0xfe, 0x78, 0xcd, 0x5a, 0xf4,
    0x1f, 0xdd, 0xa8, 0x33, 0x88, 0x07, 0xc7, 0x31, 0xb1, 0x12, 0x10, 0x59,
    0x27, 0x80, 0xec, 0x5f, 0x60, 0x51, 0x7f, 0xa9, 0x19, 0xb5, 0x4a, 0x0d,
    0x2d, 0xe5, 0x7a, 0x9f, 0x93, 0xc9, 0x9c, 0xef, 0xa0, 0xe0, 0x3b, 0x4d,
    0xae, 0x2a, 0xf5, 0xb0, 0xc8, 0xeb, 0xbb, 0x3c, 0x83, 0x53, 0x99, 0x61,
    0x17, 0x2b, 0x04, 0x7e, 0xba, 0x77, 0xd6, 0x26, 0xe1, 0x69, 0x14, 0x63,
    0x55, 0x21, 0x0c, 0x7d,
)

_RCON = (0x00, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36)


def _xtime(a: int) -> int:
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _gmul(a: int, b: int) -> int:
    p = 0
    while b:
        if b & 1:
            p ^= a
        a = _xtime(a)
        b >>= 1
    return p & 0xFF

_M9 = [_gmul(i, 0x09) for i in range(256)]
_M11 = [_gmul(i, 0x0B) for i in range(256)]
_M13 = [_gmul(i, 0x0D) for i in range(256)]
_M14 = [_gmul(i, 0x0E) for i in range(256)]


def _rot_word(w: int) -> int:
    return ((w << 8) & 0xFFFFFFFF) | ((w >> 24) & 0xFF)


def _sub_word(w: int) -> int:
    return (
        (_SBOX[(w >> 24) & 0xFF] << 24)
        | (_SBOX[(w >> 16) & 0xFF] << 16)
        | (_SBOX[(w >> 8) & 0xFF] << 8)
        | _SBOX[w & 0xFF]
    )


def _expand_key_256(key: bytes) -> list:
    words = [int.from_bytes(key[4 * i:4 * i + 4], "big") for i in range(8)]
    for i in range(8, 60):
        temp = words[i - 1]
        if i % 8 == 0:
            temp = (_sub_word(_rot_word(temp)) ^ (_RCON[i // 8] << 24)) & 0xFFFFFFFF
        elif i % 8 == 4:
            temp = _sub_word(temp)
        words.append((words[i - 8] ^ temp) & 0xFFFFFFFF)
    rounds = []
    for r in range(15):
        rounds.append(b"".join(words[4 * r + k].to_bytes(4, "big") for k in range(4)))
    return rounds


def _add_round_key(state: list, round_key: bytes) -> list:
    return [b ^ round_key[i] for i, b in enumerate(state)]


def _inv_shift_rows(state: list) -> list:
    out = [0] * 16
    for row in range(4):
        for col in range(4):
            out[row + 4 * col] = state[row + 4 * ((col - row) % 4)]
    return out


def _inv_mix_columns(state: list) -> list:
    out = [0] * 16
    for col in range(4):
        t0, t1, t2, t3 = state[4 * col], state[4 * col + 1], state[4 * col + 2], state[4 * col + 3]
        out[4 * col] = _M14[t0] ^ _M11[t1] ^ _M13[t2] ^ _M9[t3]
        out[4 * col + 1] = _M9[t0] ^ _M14[t1] ^ _M11[t2] ^ _M13[t3]
        out[4 * col + 2] = _M13[t0] ^ _M9[t1] ^ _M14[t2] ^ _M11[t3]
        out[4 * col + 3] = _M11[t0] ^ _M13[t1] ^ _M9[t2] ^ _M14[t3]
    return out


def _decrypt_block(block: bytes, round_keys: list) -> bytes:
    state = _add_round_key(list(block), round_keys[14])
    for rnd in range(13, 0, -1):
        state = _inv_shift_rows(state)
        state = [_INV_SBOX[b] for b in state]
        state = _add_round_key(state, round_keys[rnd])
        state = _inv_mix_columns(state)
    state = _inv_shift_rows(state)
    state = [_INV_SBOX[b] for b in state]
    state = _add_round_key(state, round_keys[0])
    return bytes(state)


def _aes256_cbc_decrypt(key: bytes, iv: bytes, data: bytes):
    if len(key) != 32 or len(iv) != 16 or not data or len(data) % 16:
        raise ValueError("bad AES-256-CBC input lengths")
    round_keys = _expand_key_256(bytes(key))
    out = bytearray()
    prev = bytes(iv)
    for i in range(0, len(data), 16):
        block = bytes(data[i:i + 16])
        dec = _decrypt_block(block, round_keys)
        out += bytes(a ^ b for a, b in zip(dec, prev))
        prev = block
    pad = out[-1]
    if 1 <= pad <= 16 and all(b == pad for b in out[-pad:]):
        del out[-pad:]
    return bytes(out)


def _decode_script_string(value: str) -> str:
    def _rep(m):
        uni, hx, esc = m.group(1), m.group(2), m.group(3)
        if uni:
            return chr(int(uni, 16))
        if hx:
            return chr(int(hx, 16))
        return {"b": "\b", "n": "\n", "f": "\f", "r": "\r",
                "t": "\t", "v": "\v", "0": "\0"}.get(esc, esc)
    return re.sub(r"\\u([\dA-Fa-f]{4})|\\x([\dA-Fa-f]{2})|\\([\\'\"bnfrtv0])", _rep, value)


def _script_strings(script: str) -> list:
    strings = []
    i, n, prev = 0, len(script), ""
    while i < n:
        ch = script[i]
        nxt = script[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = script.find("\n", i + 2)
            if j < 0:
                break
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = script.find("*/", i + 2)
            if j < 0:
                break
            i = j + 2
            continue
        if ch == "/" and re.match(r"[=(:,[!&|?{};]", prev):
            i += 1
            in_class = False
            while i < n:
                if script[i] == "\\":
                    i += 2
                    continue
                if script[i] == "[":
                    in_class = True
                if script[i] == "]":
                    in_class = False
                if script[i] == "/" and not in_class:
                    i += 1
                    while i < n and re.match(r"[a-z]", script[i], re.IGNORECASE):
                        i += 1
                    break
                i += 1
            continue
        if ch in ("'", '"'):
            quote_char = ch
            val = ""
            i += 1
            while i < n and script[i] != quote_char:
                if script[i] == "\\" and i + 1 < n:
                    val += script[i]
                    i += 1
                val += script[i]
                i += 1
            strings.append(_decode_script_string(val))
            i += 1
            continue
        if ch == "`":
            i += 1
            while i < n and script[i] != "`":
                i += 2 if script[i] == "\\" else 1
            i += 1
            continue
        if not ch.isspace():
            prev = ch
        i += 1
    seen, out = set(), []
    for s in strings:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _megaplay_routes(script: str):
    routes = sorted(
        [s for s in _script_strings(script)
         if re.match(r"^stream/getSources[\w/-]*$", s, re.IGNORECASE)],
        key=len,
    )
    legacy = routes[0] if routes else None
    modern = next((r for r in routes if r != legacy and legacy and r.startswith(legacy)), None)
    return legacy, modern


def _megaplay_decrypt(value: str, script: str):
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        encrypted = base64.urlsafe_b64decode(padded)
    except ValueError:
        return None
    if not encrypted or len(encrypted) % 16:
        return None
    values = [s for s in _script_strings(script) if 0 < len(s.encode("utf-8")) <= 32]
    ivs = [s for s in values if len(s.encode("utf-8")) == 16]
    for key_value in values:
        key = key_value.encode("utf-8")[:32].ljust(32, b"\0")
        for iv_value in ivs:
            try:
                raw = _aes256_cbc_decrypt(key, iv_value.encode("utf-8"), bytes(encrypted))
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            source = (data.get("file") or data.get("url")) if isinstance(data, dict) else None
            if isinstance(source, str) and source:
                return source
    return None


def _build_source_url(origin: str, path: str, file_id: str) -> str:
    base = origin.rstrip("/") + "/"
    url = urljoin(base, path.lstrip("/"))
    sep = "&" if "?" in url else "?"

    return url + sep + "id=" + quote(file_id, safe="") + "&id=" + quote(file_id, safe="")


def _map_track(track: dict, source: str) -> dict:
    label = track.get("label") or ""
    lang_key = label.lower().split(" ")[0] if label else ""
    default = track.get("default")
    return {
        "url": track.get("file"),
        "label": label or "English",
        "srclang": LANG_MAP.get(lang_key, "en"),
        "default": default if default is not None else False,
        "source": source,
    }


async def _extract_megaplay_details(embed_url: str):
    try:
        page = urlparse(str(embed_url))
        origin = page.scheme + "://" + page.netloc
        page_href = str(embed_url)
        html = await fetch_text(page_href, {"Referer": origin + "/"})
        m = re.search(r"data-id=[\"']([^\"']+)[\"']", html, re.IGNORECASE)
        if not m:
            return None
        file_id = m.group(1)
        script_urls = []
        for sm in re.finditer(r"<script[^>]+src=[\"']([^\"']+)[\"']", html, re.IGNORECASE):
            src = decode_entities(sm.group(1))
            if src.startswith("//"):
                script_urls.append(page.scheme + ":" + src)
            elif re.match(r"^https?://", src, re.IGNORECASE):
                script_urls.append(src)
            else:
                script_urls.append(urljoin(origin + "/", src))

        async def _one(u):
            try:
                return await fetch_text(u, {"Referer": page_href})
            except Exception:
                return None

        scripts = await asyncio.gather(*[_one(u) for u in script_urls]) if script_urls else []
        script = next(
            (s for s in scripts
             if s and re.search(r"getSources", s, re.IGNORECASE) and re.search(r"AES-CBC", s)),
            None,
        )
        if not script:
            return None
        legacy, modern = _megaplay_routes(script)
        if not legacy and not modern:
            return None
        headers = {"Referer": page_href, "X-Requested-With": "XMLHttpRequest"}

        async def _js(url):
            if not url:
                return None
            try:
                return await fetch_json(url, headers)
            except Exception:
                return None

        modern_data, legacy_data = await asyncio.gather(
            _js(_build_source_url(origin, modern, file_id) if modern else None),
            _js(_build_source_url(origin, legacy, file_id) if legacy else None),
        )
        legacy_url = None
        if isinstance(legacy_data, dict):
            legacy_url = ((legacy_data.get("sources") or {}).get("file")
                          or _megaplay_decrypt(legacy_data.get("enc"), script))
        sources, seen_urls = [], set()
        modern_url = ((modern_data.get("sources") or {}).get("file")
                      if isinstance(modern_data, dict) else None)
        for url, variant in ((modern_url, "modern"), (legacy_url, "legacy")):
            if url and url not in seen_urls:
                seen_urls.add(url)
                sources.append({"url": url, "variant": variant})
        if not sources:
            return None
        metadata = modern_data if isinstance(modern_data, dict) else (
            legacy_data if isinstance(legacy_data, dict) else {})
        tracks = metadata.get("tracks") if isinstance(metadata.get("tracks"), list) else []
        return {
            "origin": origin,
            "sources": sources,
            "tracks": tracks,
            "intro": metadata.get("intro"),
            "outro": metadata.get("outro"),
        }
    except Exception:
        return None


async def search(query: str) -> list:
    html = await fetch_text(f"{BASE}/filter?keyword={quote(query)}", {"Referer": BASE + "/"})
    candidates = []
    for m in re.finditer(r"<a\b[^>]*>[\s\S]*?</a>", html, re.IGNORECASE):
        tag_m = re.search(r"<a\b[^>]*>", m.group(0), re.IGNORECASE)
        tag = tag_m.group(0) if tag_m else ""
        classes = attr(tag, "class").split()
        if "name" not in classes or "d-title" not in classes:
            continue
        href = attr(tag, "href")
        sm = re.search(r"/watch/([^/?#\"']+)", href)
        if not sm:
            continue
        slug = sm.group(1)
        if slug in ("filter", "watch"):
            continue
        inner = m.group(0)[len(tag_m.group(0)) if tag_m else 0:]
        name = strip_tags(inner)
        candidates.append({"slug": slug, "text": name or slug.replace("-", " ")})
    if not candidates:
        for m in re.finditer(r"<a\b[^>]*>[\s\S]*?</a>", html, re.IGNORECASE):
            tag_m = re.search(r"<a\b[^>]*>", m.group(0), re.IGNORECASE)
            href = attr(tag_m.group(0) if tag_m else "", "href")
            sm = re.search(r"anikototv\.to/watch/([^/?#\"']+)", href)
            if sm and sm.group(1) not in ("filter", "watch"):
                candidates.append({"slug": sm.group(1), "text": sm.group(1).replace("-", " ")})
    seen, out = set(), []
    for c in candidates:
        if c["slug"] not in seen:
            seen.add(c["slug"])
            out.append(c)
    return out


async def _fetch_show_id(slug: str) -> str:
    html = await fetch_text(f"{BASE}/watch/{slug}", {"Referer": BASE + "/"})
    m = re.search(r'data-id="(\d+)"', html)
    if not m:
        raise RuntimeError(f"Could not find show ID for slug: {slug}")
    return m.group(1)


def _data_attr(tag: str, name: str) -> str:
    m = re.search(r"data-" + name + r'=\"([^\"]*)\"', tag)
    return m.group(1) if m else ""


async def scrape_series(slug: str) -> list:
    show_id = await _fetch_show_id(slug)
    data = await fetch_json(
        f"{BASE}/ajax/episode/list/{show_id}",
        {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/watch/{slug}"},
    )
    html = (data.get("result") if isinstance(data, dict) else None) or ""
    episodes = []
    for m in re.finditer(r"<a\s+[^>]*data-id=\"[^\"]*\"[^>]*>[\s\S]*?</a>", html, re.IGNORECASE):
        tag_m = re.search(r"<a\b[^>]*>", m.group(0), re.IGNORECASE)
        tag = tag_m.group(0) if tag_m else ""
        num_str = _data_attr(tag, "num")
        if not num_str:
            continue
        try:
            num = int(num_str)
        except ValueError:
            continue
        tm = re.search(r'<span class=\"d-title\"[^>]*>([\s\S]*?)</span>', m.group(0), re.IGNORECASE)
        title = strip_tags(tm.group(1)) if tm else ""
        episodes.append({
            "number": num,
            "title": title or f"Episode {num}",
            "hasSub": _data_attr(tag, "sub") == "1",
            "hasDub": _data_attr(tag, "dub") == "1",
        })
    episodes.sort(key=lambda e: e["number"])
    seen, out = set(), []
    for e in episodes:
        if e["number"] not in seen:
            seen.add(e["number"])
            out.append(e)
    return out


async def resolve_series(anilist_id: int, ctx: dict | None = None) -> dict:
    ctx = ctx or await build_ctx(int(anilist_id))
    key = f"np:anikoto:{int(anilist_id)}"
    hit = _cache.cached(key, _cache.SHOW_IDENTITY_TTL)
    if hit is not None:
        return hit
    media = ctx["media"]
    titles = build_titles(media, ctx.get("anizip"))
    candidates = await find_top_slugs(titles, search)
    expected = expected_count(media, ctx.get("anizip"))
    try:
        offset = await get_prequel_offset(int(anilist_id))
    except Exception:
        offset = 0
    selected = await select_series(candidates, scrape_series, expected, media.get("status"), offset)
    if not selected:
        raise RuntimeError(f"Anikoto match not found for AniList {anilist_id}")
    show_id = await _fetch_show_id(selected["slug"])
    data = {"slug": selected["slug"], "show_id": show_id, "title": selected["title"],
            "mode": selected["mode"], "offset": offset, "score": selected["score"]}
    _cache.set(key, data, _cache.SHOW_IDENTITY_TTL)
    return data


def _build_lists(anilist_id: int, series: dict, provider_eps: list, ctx: dict, expected) -> dict:
    sub, dub = [], []
    for src in provider_eps:
        number = src["number"] - series["offset"] if series["mode"] == "offset" else src["number"]
        if number < 1:
            continue
        if expected and number > expected:
            continue
        meta = episode_meta(number, ctx)
        base = {
            "number": number,
            "title": meta["title"] or src.get("title") or f"Episode {number}",
            "duration": meta["duration"],
            "filler": meta["filler"],
            "uncensored": meta["uncensored"],
            "description": meta["description"],
            "image": meta["image"],
            "airDate": meta["airDate"],
            "sourceNumber": src["number"],
        }
        if src.get("hasSub"):
            sub.append({"id": f"watch/anikoto/{anilist_id}/sub/anikoto-{number}", **base, "audio": "sub"})
        if src.get("hasDub"):
            dub.append({"id": f"watch/anikoto/{anilist_id}/dub/anikoto-{number}", **base, "audio": "dub"})
    return {"sub": sub, "dub": dub}


async def get_episodes(anilist_id: int, ctx: dict | None = None) -> dict:
    ctx = ctx or await build_ctx(int(anilist_id))
    media = ctx["media"]
    series = await resolve_series(int(anilist_id), ctx)
    episodes = await scrape_series(series["slug"])
    expected = expected_count(media, ctx.get("anizip"))
    return {
        "meta": {
            "id": series["slug"],
            "title": series["title"],
            "source": NAME,
            "matchScore": round(series["score"], 3),
            "numbering": series["mode"],
            "episodeOffset": series["offset"] if series["mode"] == "offset" else 0,
        },
        "episodes": _build_lists(int(anilist_id), series, episodes, ctx, expected),
    }


def _num(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return int(f) if float(f).is_integer() else f


def _split_servers(server_html: str, audio: str):
    server_items, download_items = [], []
    for tm in re.finditer(
            r'<div class=\"type\" data-type=\"([^\"]+)\">([\s\S]*?)</ul>\s*</div>',
            server_html, re.IGNORECASE):
        type_name = tm.group(1)
        for li in re.finditer(
                r"<li\s+([^>]*data-link-id[^>]*)>([\s\S]*?)</li>",
                tm.group(2), re.IGNORECASE):
            lm = re.search(r'data-link-id=\"([^\"]+)\"', li.group(1))
            if not lm:
                continue
            name = strip_tags(li.group(2))
            if type_name == "dl" or "download" in name.lower() or "kiwi" in name.lower():
                download_items.append({"linkId": lm.group(1), "name": name})
            elif type_name == audio:
                server_items.append({"linkId": lm.group(1), "name": name})
    return server_items, download_items


def _merge_mapper(mapper_data, audio: str, server_items: list, download_items: list):
    if not isinstance(mapper_data, dict):
        return
    for s_key, s_obj in mapper_data.items():
        if s_key == "status" or not isinstance(s_obj, dict):
            continue
        clean = re.sub(r"[-_]+$", "", s_key).strip()
        entry = s_obj.get(audio)
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if isinstance(url, str) and url:
            server_items.append({"linkId": url, "name": clean})
        downloads = entry.get("download")
        if isinstance(downloads, dict):
            for _label, durl in downloads.items():
                if isinstance(durl, str) and durl:
                    download_items.append({"url": durl, "name": clean})


async def _resolve_server_url(link_id: str):
    if link_id.startswith("http"):
        return {"result": {"url": link_id}}
    try:
        return await fetch_json(
            f"{BASE}/ajax/server?get={quote(link_id, safe='')}",
            {"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/"},
        )
    except Exception:
        return None


async def _scrape_episode_watch(anilist_id: int, audio: str, ep_num: int, ctx: dict) -> list:
    series = await resolve_series(anilist_id, ctx)
    provider_ep = int(ep_num) + series["offset"] if series["mode"] == "offset" else int(ep_num)
    audios = ["sub", "dub"] if audio == "all" else [audio]
    show_id = series.get("show_id") or await _fetch_show_id(series["slug"])
    list_json = await fetch_json(
        f"{BASE}/ajax/episode/list/{show_id}",
        {"X-Requested-With": "XMLHttpRequest", "Referer": f"{BASE}/watch/{series['slug']}"},
    )
    html = (list_json.get("result") if isinstance(list_json, dict) else None) or ""
    target = None
    for m in re.finditer(r"<a\s+[^>]*data-id=\"[^\"]*\"[^>]*>", html, re.IGNORECASE):
        tag = m.group(0)
        try:
            num = int(_data_attr(tag, "num"))
        except ValueError:
            continue
        if num == provider_ep:
            target = {"ids": _data_attr(tag, "ids"), "mal": _data_attr(tag, "mal"),
                      "slug": _data_attr(tag, "slug"), "timestamp": _data_attr(tag, "timestamp")}
            break
    if not target or not target["ids"]:
        raise RuntimeError(f"Episode {provider_ep} not found for show: {series['title']}")

    async def _servers():
        try:
            return await fetch_json(
                f"{BASE}/ajax/server/list?servers={quote(target['ids'], safe='')}",
                {"X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/"},
            )
        except Exception:
            return None

    async def _mapper():
        if target["mal"] and target["slug"] and target["timestamp"]:
            try:
                return await fetch_json(
                    f"{MAPPER}/{target['mal']}/{target['slug']}/{target['timestamp']}",
                    {"Referer": BASE + "/"},
                )
            except Exception:
                return None
        return None

    server_data, mapper_data = await asyncio.gather(_servers(), _mapper())
    server_html = (server_data.get("result") if isinstance(server_data, dict) else None) or ""

    streams, download_pool, sub_seen = [], [], set()
    for aud in audios:
        server_items, download_items = _split_servers(server_html, aud)
        _merge_mapper(mapper_data, aud, server_items, download_items)
        for dl in download_items:
            if dl not in download_pool:
                download_pool.append((aud, dl))
        seen_names = set()
        for item in server_items:
            if item["name"] in seen_names:
                continue
            seen_names.add(item["name"])
            resolved = await _resolve_server_url(item["linkId"])
            embed_url = (resolved.get("result") or {}).get("url") if isinstance(resolved, dict) else None
            if not embed_url:
                continue
            server_intro = {"start": 0, "end": 0}
            server_outro = {"start": 0, "end": 0}
            if isinstance(resolved, dict):
                skip_data = (resolved.get("result") or {}).get("skip_data") or {}
                for key, dest in (("intro", server_intro), ("outro", server_outro)):
                    val = skip_data.get(key)
                    if isinstance(val, (list, tuple)) and len(val) == 2:
                        s, e = _num(val[0]), _num(val[1])
                        if s or e:
                            dest["start"], dest["end"] = s, e
            hls_sources = []
            if "#aHR0c" in embed_url:
                frag = embed_url.split("#", 1)[1]
                try:
                    decoded = base64.b64decode(frag + "=" * (-len(frag) % 4)).decode("utf-8", "errors")
                    if ".m3u8" in decoded:
                        hls_sources.append({"url": decoded, "variant": None})
                except ValueError:
                    pass
            extracted = await _extract_megaplay_details(embed_url)
            item_subs = []
            if extracted:
                for source in extracted["sources"]:
                    if not any(h["url"] == source["url"] for h in hls_sources):
                        hls_sources.append(source)
                for t in extracted["tracks"]:
                    if not isinstance(t, dict):
                        continue
                    mapped = _map_track(t, item["name"])
                    if mapped.get("url") and mapped["url"] not in sub_seen:
                        sub_seen.add(mapped["url"])
                        item_subs.append(mapped)
                for key, dest in (("intro", server_intro), ("outro", server_outro)):
                    val = extracted.get(key) or {}
                    if isinstance(val, dict):
                        s, e = _num(val.get("start")), _num(val.get("end"))
                        if s or e:
                            dest["start"], dest["end"] = s, e
            try:
                parsed = urlparse(embed_url)
                embed_origin = parsed.scheme + "://" + parsed.netloc + "/"
            except Exception:
                embed_origin = BASE + "/"
            referer = (extracted["origin"] + "/" if extracted and extracted.get("origin")
                       else embed_origin)
            if hls_sources:
                for source in hls_sources:
                    obj = {
                        "url": source["url"],
                        "type": "hls",
                        "server": item["name"],
                        "audio": aud,
                        "embed": embed_url,
                        "referer": referer,
                        "subtitles": item_subs,
                        "priority": 5 if not streams else 4,
                        "isActive": not streams,
                    }
                    if source.get("variant"):
                        obj["variant"] = source["variant"]
                    if server_intro["start"] or server_intro["end"]:
                        obj["intro"] = dict(server_intro)
                    if server_outro["start"] or server_outro["end"]:
                        obj["outro"] = dict(server_outro)
                    streams.append(obj)
                streams.append({
                    "url": embed_url,
                    "type": "embed",
                    "server": item["name"],
                    "audio": aud,
                    "referer": embed_origin,
                    "priority": 4,
                    "isActive": False,
                })
            else:
                obj = {
                    "url": embed_url,
                    "type": "embed",
                    "server": item["name"],
                    "audio": aud,
                    "referer": embed_origin,
                    "priority": 4,
                    "isActive": not streams,
                }
                if server_intro["start"] or server_intro["end"]:
                    obj["intro"] = dict(server_intro)
                if server_outro["start"] or server_outro["end"]:
                    obj["outro"] = dict(server_outro)
                streams.append(obj)

    dl_seen = set()
    for aud, dl in download_pool:
        durl = dl.get("url")
        if not durl and dl.get("linkId"):
            resolved = await _resolve_server_url(dl["linkId"])
            durl = (resolved.get("result") or {}).get("url") if isinstance(resolved, dict) else None
        if durl and durl not in dl_seen:
            dl_seen.add(durl)
            streams.append({
                "url": durl,
                "type": "mp4",
                "server": dl.get("name", "Download"),
                "audio": aud,
                "referer": BASE + "/",
                "priority": 1,
                "isActive": False,
            })
    return streams


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict | None = None) -> list:
    if audio not in ("sub", "dub", "all"):
        raise ValueError("audio must be sub, dub or all")
    ctx = ctx or await build_ctx(int(anilist_id))
    return await _scrape_episode_watch(int(anilist_id), audio, int(ep), ctx)
