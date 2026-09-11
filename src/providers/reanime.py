import asyncio
import base64
import hashlib
import re
from urllib.parse import urlencode, urlparse

from src.providers import _cache
from src.providers._http import fetch_json, fetch_text
from src.providers._match import build_titles, episode_meta, expected_count
from src.providers._media import build_ctx

NAME = "reanime"
BASE = "https://reanime.to"
FLIX = "https://flixcloud.cc"

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


def _sha256_hex(value) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(bytes(value)).hexdigest()


def _b64_to_bytes(value) -> bytes:
    if not value:
        return b""
    if not isinstance(value, str):
        raise RuntimeError("expected base64 string")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4))
    except ValueError as e:
        raise RuntimeError(f"bad base64 payload: {e}")


def _derive_fields(seed: str) -> dict:
    first = seed
    for i in range(3):
        first = _sha256_hex(first + str(i))
    second = first
    for i in range(3):
        second = _sha256_hex(second + str(i))
    return {
        "keyField": "kf_" + first[8:16],
        "ivField": "ivf_" + first[16:24],
        "containerName": "cd_" + first[24:32],
        "arrayName": "ad_" + first[32:40],
        "objectName": "od_" + first[40:48],
        "tokenField": first[48:64] + "_" + first[56:64],
        "keyFrag2Field": second[0:16] + "_" + second[16:24],
    }


def _extract_ssr_obj(html: str) -> str:
    m = re.search(r'\{type:"data",data:(\{)', html)
    if not m:
        raise RuntimeError("SSR data block not found")
    start = m.start(1)
    depth = 0
    for i in range(start, len(html)):
        if html[i] == "{":
            depth += 1
        elif html[i] == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
    raise RuntimeError("SSR brace matching failed")


def _parse_js_literal(source: str):
    idx = 0
    n = len(source)

    def _ws():
        nonlocal idx
        while idx < n and source[idx] in (" ", "\t", "\n", "\r", "\f", "\v"):
            idx += 1

    def _dq():
        nonlocal idx
        out = ""
        idx += 1
        while idx < n and source[idx] != '"':
            if source[idx] == "\\":
                idx += 1
                out += {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(
                    source[idx], source[idx])
                idx += 1
            else:
                out += source[idx]
                idx += 1
        idx += 1
        return out

    def _sq():
        nonlocal idx
        out = ""
        idx += 1
        while idx < n and source[idx] != "'":
            if source[idx] == "\\":
                idx += 1
                if source[idx] == "'":
                    out += "'"
                else:
                    out += {"n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(
                        source[idx], source[idx])
                idx += 1
            else:
                out += source[idx]
                idx += 1
        idx += 1
        return out

    def _key():
        nonlocal idx
        _ws()
        if idx < n and source[idx] == '"':
            return _dq()
        if idx < n and source[idx] == "'":
            return _sq()
        m = re.match(r"[a-zA-Z_$][a-zA-Z0-9_$]*", source[idx:])
        if not m:
            raise RuntimeError(f"Bad key at pos {idx}: {source[idx:idx + 20]}")
        idx += len(m.group(0))
        return m.group(0)

    def _obj():
        nonlocal idx
        out = {}
        idx += 1
        _ws()
        while idx < n and source[idx] != "}":
            if source[idx] == ",":
                idx += 1
                _ws()
                continue
            prop = _key()
            _ws()
            idx += 1
            out[prop] = _val()
            _ws()
        idx += 1
        return out

    def _arr():
        nonlocal idx
        out = []
        idx += 1
        _ws()
        while idx < n and source[idx] != "]":
            if source[idx] == ",":
                idx += 1
                _ws()
                continue
            out.append(_val())
            _ws()
        idx += 1
        return out

    def _val():
        nonlocal idx
        _ws()
        if idx >= n:
            raise RuntimeError("unexpected end of JS literal")
        ch = source[idx]
        if ch == "{":
            return _obj()
        if ch == "[":
            return _arr()
        if ch == '"':
            return _dq()
        if ch == "'":
            return _sq()
        for lit, val in (("true", True), ("false", False), ("null", None),
                         ("undefined", None), ("!0", True), ("!1", False)):
            if source.startswith(lit, idx):
                idx += len(lit)
                return val
        m = re.match(r"-?[\d.]+([eE][+-]?\d+)?", source[idx:])
        if m:
            idx += len(m.group(0))
            try:
                return float(m.group(0))
            except ValueError:
                raise RuntimeError(f"bad number at pos {idx}")
        raise RuntimeError(f"JS parse error at pos {idx}: ...{source[idx:idx + 20]}")

    return _val()


def _read_leb(buf, pos: int):
    value, shift = 0, 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 127) << shift
        shift += 7
        if not byte & 128:
            return value, pos


def _parse_wasm_decrypt(raw: bytes):
    data = bytes(raw)
    pos = 8
    while pos < len(data):
        section = data[pos]
        pos += 1
        size, pos = _read_leb(data, pos)
        if section == 10:
            pos += 1
            body_size, pos = _read_leb(data, pos)
            pos += body_size
            break
        pos += size
    func_size, pos = _read_leb(data, pos)
    body = data[pos:pos + func_size]
    xor_end = bytes([32, 2, 32, 5, 106, 45, 0, 0, 115, 33, 6])
    found = body.find(xor_end)
    if found < 0:
        raise RuntimeError("WASM: transform start not found")
    t_start = found + len(xor_end)
    t_end, step = -1, 36
    i = t_start
    while i < len(body) - 4:
        if body[i] == 32 and body[i + 1] == 5 and body[i + 2] == 65:
            val, nxt = _read_leb(body, i + 3)
            if nxt < len(body) and body[nxt] == 108:
                t_end, step = i, val
                break
        i += 1
    if t_end < 0:
        raise RuntimeError("WASM: keystream not found")
    code = body[t_start:t_end]

    def _transform(byte: int) -> int:
        local = byte & 255
        stack = []
        p = 0
        while p < len(code):
            op = code[p]
            p += 1
            if op == 32:
                li, p = _read_leb(code, p)
                stack.append(local if li == 6 else 0)
            elif op == 33:
                li, p = _read_leb(code, p)
                val = stack.pop()
                if li == 6:
                    local = val & 255
            elif op == 65:
                val, p = _read_leb(code, p)
                stack.append(val)
            elif op in (106, 107, 113, 114, 115, 116, 118):
                right = stack.pop()
                left = stack.pop()
                if op == 106:
                    stack.append((left + right) & 255)
                elif op == 107:
                    stack.append((left - right + 256) & 255)
                elif op == 113:
                    stack.append((left & right) & 255)
                elif op == 114:
                    stack.append((left | right) & 255)
                elif op == 115:
                    stack.append(left ^ (right & 255))
                elif op == 116:
                    stack.append((left << (right & 7)) & 255)
                elif op == 118:
                    stack.append((left >> (right & 7)) & 255)
        return local

    return step, _transform


def _run_decrypt(wasm_bytes: bytes, fragment: bytes, key_fragment: bytes,
                 token: bytes, seed_number: int) -> bytes:
    step, transform = _parse_wasm_decrypt(wasm_bytes)
    out = bytearray(len(fragment))
    for i in range(len(fragment)):
        value = (fragment[i] ^ key_fragment[i] ^ token[i]) & 255
        out[i] = (transform(value) ^ ((i * step + seed_number) & 255)) & 255
    return bytes(out)


async def _extract_flixcloud(embed_html: str, referer: str | None = None) -> dict:
    data = _parse_js_literal(_extract_ssr_obj(embed_html))
    if not isinstance(data, dict):
        raise RuntimeError("SSR data is not an object")
    seed = data.get("obfuscation_seed")
    if not seed:
        raise RuntimeError("obfuscation_seed missing")
    fields = _derive_fields(seed)
    crypto_data = data.get("obfuscated_crypto_data")
    if not isinstance(crypto_data, dict):
        raise RuntimeError("obfuscated_crypto_data missing")
    container = crypto_data.get(fields["containerName"])
    if not isinstance(container, dict):
        raise RuntimeError("crypto container missing: " + fields["containerName"])
    array = container.get(fields["arrayName"])
    if not isinstance(array, list) or not array or not isinstance(array[0], dict):
        raise RuntimeError("crypto array missing: " + fields["arrayName"])
    obj = array[0].get(fields["objectName"])
    if not isinstance(obj, dict):
        raise RuntimeError("crypto object missing: " + fields["objectName"])
    fragment = _b64_to_bytes(obj.get(fields["keyField"]))
    iv = _b64_to_bytes(obj.get(fields["ivField"]))
    key_fragment = _b64_to_bytes(data.get(fields["keyFrag2Field"]))
    if not key_fragment:
        raise RuntimeError("key fragment missing: " + fields["keyFrag2Field"])
    token = data.get(fields["tokenField"])
    if not token:
        raise RuntimeError("token missing: " + fields["tokenField"])
    token_data = await fetch_json(
        FLIX + "/api/m3u8/" + token, {"Referer": referer or (BASE + "/")})
    if not isinstance(token_data, dict):
        raise RuntimeError("token API did not return an object")
    video_key = _sha256_hex(token + "vid")[:10]
    token_key = _sha256_hex(token + "key")[:10]
    video_bytes = _b64_to_bytes(token_data.get(video_key))
    token_bytes = _b64_to_bytes(token_data.get(token_key))
    if not video_bytes or not token_bytes:
        raise RuntimeError("token fields missing")
    try:
        seed_number = int(seed[:8], 16)
    except ValueError:
        raise RuntimeError("bad obfuscation_seed")
    wasm_payload = _b64_to_bytes(data.get("w_payload") or "")
    if not wasm_payload:
        raise RuntimeError("w_payload missing from embed data")
    wasm_out = _run_decrypt(wasm_payload, fragment, key_fragment, token_bytes, seed_number)
    material = hashlib.pbkdf2_hmac("sha256", wasm_out, seed.encode("utf-8"), 1000, 32)
    derived = bytes(b ^ (ord(seed[i % len(seed)]) & 255) for i, b in enumerate(material))
    aes_key = hashlib.sha256(derived).digest()
    plain = _aes256_cbc_decrypt(aes_key, bytes(iv), bytes(video_bytes))
    url = plain.decode("utf-8", "errors").strip().rstrip("\0")
    if not url.startswith("http"):
        raise RuntimeError(f"Unexpected decrypted value: {url[:60]}")
    return {
        "url": url,
        "subtitles": data.get("subtitles") or [],
        "thumbnails_vtt": data.get("thumbnails_vtt"),
        "video_title": data.get("video_title"),
        "intro_chapter": data.get("intro_chapter"),
        "outro_chapter": data.get("outro_chapter"),
        "video_id": data.get("video_id"),
    }


def _num_or_none(value):
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float)) else None


async def search(query: str) -> list:
    data = await fetch_json(BASE + "/api/v1/search?" + urlencode({"q": query, "limit": 10}))
    results = data.get("results") if isinstance(data, dict) else None
    return results if isinstance(results, list) else []


async def _fetch_anime_detail(anime_id: str):
    try:
        return await fetch_json(f"{BASE}/api/v1/anime/{anime_id}")
    except Exception:
        return None


def _cover_anilist_id(cover_image) -> int | None:
    if not isinstance(cover_image, dict):
        return None
    for key in ("extra_large", "large", "medium"):
        url = cover_image.get(key)
        if isinstance(url, str):
            m = re.search(r"anilist\.co/.*/bx(\d+)-", url)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return None
    return None


def _series_title(title, fallback: str) -> str:
    if isinstance(title, dict):
        return title.get("english") or title.get("romaji") or fallback
    return fallback


async def resolve_series(anilist_id: int, ctx: dict | None = None) -> dict:
    anilist_id = int(anilist_id)
    ctx = ctx or await build_ctx(anilist_id)
    key = f"np:reanime:{anilist_id}"
    hit = _cache.cached(key, _cache.SHOW_IDENTITY_TTL)
    if hit is not None:
        return hit
    media = ctx["media"]
    mal_id = media.get("idMal")
    queries = build_titles(media, ctx.get("anizip"))[:5]

    async def _one(q):
        try:
            return await search(q)
        except Exception:
            return []

    found = await asyncio.gather(*[_one(q) for q in queries]) if queries else []
    candidates: dict = {}
    for results in found:
        for r in results or []:
            if isinstance(r, dict) and r.get("anime_id") and r["anime_id"] not in candidates:
                candidates[r["anime_id"]] = r

    for rid, r in candidates.items():
        if _cover_anilist_id(r.get("cover_image")) == anilist_id:
            data = {
                "animeId": rid,
                "title": _series_title(r.get("title"), rid),
                "anilistId": anilist_id,
                "malId": None,
                "subbed": _num_or_none(r.get("subbed")),
                "dubbed": _num_or_none(r.get("dubbed")),
                "episodesCount": _num_or_none(r.get("episodes")),
                "matchType": "cover_image",
                "matchScore": 1.0,
            }
            _cache.set(key, data, _cache.SHOW_IDENTITY_TTL)
            return data

    need_detail = [rid for rid, r in candidates.items()
                   if _cover_anilist_id(r.get("cover_image")) is None]

    async def _det(rid):
        return rid, await _fetch_anime_detail(rid)

    details = await asyncio.gather(*[_det(rid) for rid in need_detail]) if need_detail else []

    for rid, detail in details:
        if isinstance(detail, dict) and detail.get("anilist_id") is not None:
            try:
                detail_al = int(detail["anilist_id"])
            except (TypeError, ValueError):
                continue
            if detail_al == anilist_id:
                data = {
                    "animeId": rid,
                    "title": _series_title(detail.get("title"),
                                          _series_title((candidates[rid] or {}).get("title"), rid)),
                    "anilistId": anilist_id,
                    "malId": detail.get("mal_id"),
                    "subbed": _num_or_none(detail.get("subbed")),
                    "dubbed": _num_or_none(detail.get("dubbed")),
                    "episodesCount": _num_or_none(detail.get("episodes")),
                    "matchType": "anilist",
                    "matchScore": 1.0,
                }
                _cache.set(key, data, _cache.SHOW_IDENTITY_TTL)
                return data

    if mal_id is not None:
        for rid, detail in details:
            if not isinstance(detail, dict) or detail.get("mal_id") is None:
                continue
            try:
                detail_mal = int(detail["mal_id"])
            except (TypeError, ValueError):
                continue
            if detail_mal == int(mal_id):
                data = {
                    "animeId": rid,
                    "title": _series_title(detail.get("title"), rid),
                    "anilistId": anilist_id,
                    "malId": detail_mal,
                    "subbed": _num_or_none(detail.get("subbed")),
                    "dubbed": _num_or_none(detail.get("dubbed")),
                    "episodesCount": _num_or_none(detail.get("episodes")),
                    "matchType": "mal",
                    "matchScore": 0.9,
                }
                _cache.set(key, data, _cache.SHOW_IDENTITY_TTL)
                return data

    raise RuntimeError(f"No confirmed reanime match for AniList {anilist_id}")


async def _fetch_episodes_list(anime_id: str, limit: int = 2000) -> list:
    data = await fetch_json(
        f"{BASE}/api/v1/anime/{anime_id}/episodes?" + urlencode({"limit": limit}))
    eps = data.get("data") if isinstance(data, dict) else None
    return eps if isinstance(eps, list) else []


def _merge_episode(anilist_id: int, ep: dict, ctx: dict, audio: str) -> dict:
    number = ep.get("episode_number")
    meta = episode_meta(number, ctx)
    duration = meta["duration"]
    if duration is None and _num_or_none(ep.get("duration")) is not None:
        duration = ep["duration"] * 60
    filler = ep.get("is_filler")
    if filler is None:
        filler = meta["filler"]
    return {
        "id": f"watch/reanime/{anilist_id}/{audio}/reanime-{number}",
        "number": number,
        "title": meta["title"] or ep.get("title") or f"Episode {number}",
        "duration": duration,
        "filler": bool(filler),
        "uncensored": False,
        "description": meta["description"] or ep.get("description"),
        "image": meta["image"] or ep.get("thumbnail"),
        "airDate": meta["airDate"] or ep.get("aired"),
        "sourceNumber": number,
        "audio": audio,
    }


async def get_episodes(anilist_id: int, ctx: dict | None = None) -> dict:
    anilist_id = int(anilist_id)
    ctx = ctx or await build_ctx(anilist_id)
    series = await resolve_series(anilist_id, ctx)
    re_eps = await _fetch_episodes_list(series["animeId"])
    if not re_eps:
        raise RuntimeError(
            f"No reanime episodes found for AniList {anilist_id} (slug {series['animeId']})")
    has_sub = series.get("subbed") is None or series.get("subbed") > 0
    dub_count = series.get("dubbed") or 0
    try:
        dub_count = int(dub_count)
    except (TypeError, ValueError):
        dub_count = 0
    sub, dub = [], []
    for ep in sorted(re_eps, key=lambda e: e.get("episode_number") or 0):
        if not isinstance(ep, dict) or ep.get("episode_number") is None:
            continue
        if has_sub:
            sub.append(_merge_episode(anilist_id, ep, ctx, "sub"))
        if dub_count > 0 and ep["episode_number"] <= dub_count:
            dub.append(_merge_episode(anilist_id, ep, ctx, "dub"))
    sub.sort(key=lambda e: e["number"])
    dub.sort(key=lambda e: e["number"])
    return {
        "meta": {
            "id": series["animeId"],
            "title": series["title"],
            "source": NAME,
            "matchScore": series.get("matchScore", 1.0),
            "numbering": "local",
            "episodeOffset": 0,
        },
        "episodes": {"sub": sub, "dub": dub},
    }


async def _resolve_stream(anilist_id: int, audio: str, ep: int, ctx: dict | None = None):
    series = await resolve_series(anilist_id, ctx)
    title = series["title"]
    slug = series["animeId"]
    order = {"HD-2": 0, "HD-1": 1}

    async def _watch_api():
        try:
            return await fetch_json(f"{BASE}/api/watch/{slug}/{ep}")
        except Exception:
            return None

    async def _flix_api():
        try:
            return await fetch_json(f"{BASE}/api/flix/{anilist_id}/{ep}")
        except Exception:
            return None

    watch_data, flix_data = await asyncio.gather(_watch_api(), _flix_api())
    links = list((watch_data or {}).get("episode_links") or []) if isinstance(watch_data, dict) else []
    if isinstance(flix_data, dict) and flix_data.get("success") and isinstance(
            flix_data.get("servers"), list):
        seen_ids = {s.get("$id") for s in links if isinstance(s, dict)}
        for s in flix_data["servers"]:
            if isinstance(s, dict) and s.get("$id") not in seen_ids:
                seen_ids.add(s.get("$id"))
                links.append(s)
    if audio == "sub":
        audio_types = ("sub", "s-sub")
    elif audio == "dub":
        audio_types = ("dub", "s-dub")
    else:
        audio_types = ("sub", "s-sub", "dub", "s-dub")
    servers = sorted(
        [s for s in links if isinstance(s, dict) and s.get("dataType") in audio_types],
        key=lambda s: order.get(s.get("serverName"), 9),
    )
    if not servers:
        raise RuntimeError(f"No {audio} servers for \"{title}\" ep {ep}")
    seen, unique = set(), []
    for s in servers:
        k = (s.get("serverName"), s.get("dataType"), s.get("dataLink"))
        if k not in seen:
            seen.add(k)
            unique.append(s)

    async def _decrypt_one(server, index):
        try:
            embed_html = await fetch_text(server["dataLink"], {"Referer": BASE + "/"})
            stream = await _extract_flixcloud(embed_html, referer=BASE + "/")
            return {"server": server, "stream": stream, "index": index}
        except Exception as e:
            return {"server": server, "error": str(e), "index": index}

    decrypted = await asyncio.gather(
        *[_decrypt_one(s, i) for i, s in enumerate(unique)])
    streams = [d for d in decrypted if d.get("stream") and d["stream"].get("url")]
    if not streams:
        err = next((d.get("error") for d in decrypted if d.get("error")), "No decrypted streams")
        raise RuntimeError(err)
    return {"title": title, "slug": slug, "watchData": watch_data,
            "servers": unique, "streams": streams}


def _norm_audio(data_type: str, fallback: str) -> str:
    if isinstance(data_type, str) and "dub" in data_type.lower():
        return "dub"
    if isinstance(data_type, str) and "sub" in data_type.lower():
        return "sub"
    return fallback


async def watch(anilist_id: int, audio: str, ep: int, ctx: dict | None = None) -> list:
    if audio not in ("sub", "dub", "all"):
        raise ValueError("audio must be sub, dub or all")
    anilist_id, ep = int(anilist_id), int(ep)
    resolved = await _resolve_stream(anilist_id, audio, ep, ctx)
    total = len(resolved["streams"])
    out, seen_urls = [], set()
    for item in resolved["streams"]:
        server, stream = item["server"], item["stream"]
        if stream["url"] in seen_urls:
            continue
        seen_urls.add(stream["url"])
        try:
            parsed = urlparse(server.get("dataLink") or "")
            referer = parsed.scheme + "://" + parsed.netloc + "/" if parsed.netloc else BASE + "/"
        except Exception:
            referer = BASE + "/"
        entry = {
            "url": stream["url"],
            "type": "hls",
            "server": server.get("serverName"),
            "audio": _norm_audio(server.get("dataType"), audio if audio != "all" else "sub"),
            "embed": server.get("dataLink"),
            "referer": referer,
            "subtitles": stream.get("subtitles") or [],
            "priority": total - item["index"],
        }
        if stream.get("thumbnails_vtt") is not None:
            entry["thumbnails_vtt"] = stream["thumbnails_vtt"]
        if stream.get("video_title") is not None:
            entry["video_title"] = stream["video_title"]
        if stream.get("intro_chapter") is not None:
            entry["intro"] = stream["intro_chapter"]
        if stream.get("outro_chapter") is not None:
            entry["outro"] = stream["outro_chapter"]
        out.append(entry)
    return out
