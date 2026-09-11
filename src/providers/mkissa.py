import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
import unicodedata
from urllib.parse import quote, urljoin, urlparse

import httpx

from src.providers import _cache
from src.providers._http import UA, fetch_json, fetch_text
from src.providers._match import build_titles, decode_entities, episode_meta, expected_count
from src.providers._media import build_ctx

NAME = "mkissa"
UA4 = UA
REFERER = "https://mkissa.to"
API = "https://api.mkissa.net"
API_URL = f"{API}/api"
CONTENT_LANE = "k7"
REFERER_HOST = "mkissa.to"
KEY_GROUP = "mkissa"
BOOT_EPOCH_MS = 604800000
BOOT_GRACE_MS = 86400000
AA_REQ_MS = 300000
WATCH_MEMORY_TTL = 3 * 60 * 60
DISCOVERY_CONCURRENCY = 16
DISCOVERY_LIMIT = 600
FETCH_TIMEOUT = 10.0
EXTRACT_TIMEOUT = 5.0
CAPTCHA_RETRIES = 5

HEX_TABLE = {
    "79": "A", "7a": "B", "7b": "C", "7c": "D", "7d": "E", "7e": "F",
    "7f": "G", "70": "H", "71": "I", "72": "J", "73": "K", "74": "L",
    "75": "M", "76": "N", "77": "O", "68": "P", "69": "Q", "6a": "R",
    "6b": "S", "6c": "T", "6d": "U", "6e": "V", "6f": "W", "60": "X",
    "61": "Y", "62": "Z", "59": "a", "5a": "b", "5b": "c", "5c": "d",
    "5d": "e", "5e": "f", "5f": "g", "50": "h", "51": "i", "52": "j",
    "53": "k", "54": "l", "55": "m", "56": "n", "57": "o", "48": "p",
    "49": "q", "4a": "r", "4b": "s", "4c": "t", "4d": "u", "4e": "v",
    "4f": "w", "40": "x", "41": "y", "42": "z", "08": "0", "09": "1",
    "0a": "2", "0b": "3", "0c": "4", "0d": "5", "0e": "6", "0f": "7",
    "00": "8", "01": "9", "15": "-", "16": ".", "67": "_", "46": "~",
    "02": ":", "17": "/", "07": "?", "1b": "#", "63": "[", "65": "]",
    "78": "@", "19": "!", "1c": "$", "1e": "&", "10": "(", "11": ")",
    "12": "*", "13": "+", "14": ",", "03": ";", "05": "=", "1d": "%",
}

_crypto_config = None
_episode_query_cache = None
_session_cookies = {}
_watch_cache = {}

IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"


class NeedCaptchaError(RuntimeError):
    code = "NEED_CAPTCHA"

    def __init__(self, message="MKissa requested captcha"):
        super().__init__(message)
        self.raw_body = None


def decode_hex_url(hex_value: str) -> str:
    out = []
    for i in range(0, len(hex_value), 2):
        pair = hex_value[i:i + 2].lower()
        out.append(HEX_TABLE.get(pair, pair))
    return "".join(out)


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hmac_bytes(key, value: str) -> bytes:
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def store_cookies(headers) -> None:
    try:
        raw = headers.get_list("set-cookie")
    except Exception:
        single = headers.get("set-cookie") if hasattr(headers, "get") else None
        raw = [single] if single else []
    for value in raw or []:
        for part in re.split(r",(?=[^;,]+?=)", str(value)):
            pair = part.split(";")[0].strip() if part else ""
            index = pair.find("=")
            if index > 0:
                _session_cookies[pair[:index]] = pair[index + 1:]


def cookie_header() -> str:
    return "; ".join(f"{k}={v}" for k, v in _session_cookies.items())


def browser_headers(extra=None):
    headers = {
        "User-Agent": UA4,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "sec-ch-ua": '"Not=A?Brand";v="99", "Google Chrome";v="151", "Chromium";v="151"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }
    cookie = cookie_header()
    if cookie:
        headers["Cookie"] = cookie
    if extra:
        headers.update(extra)
    return headers


def api_headers(build_id: str, extra=None):
    headers = {
        "Referer": f"{REFERER}/",
        "Origin": REFERER,
        "x-build-id": build_id,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Priority": "u=1, i",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "cross-site",
    }
    if extra:
        headers.update(extra)
    return headers


async def _session_get_text(url, headers=None, timeout=FETCH_TIMEOUT):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        res = await client.get(url, headers=browser_headers(headers or {}))
    store_cookies(res.headers)
    if res.status_code != 200:
        raise RuntimeError(f"Fetch {res.status_code}: {url}")
    return res.text


async def _api_request(method, url, headers=None, json_body=None, timeout=FETCH_TIMEOUT):
    merged = browser_headers(headers or {})
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        if method == "POST":
            res = await client.post(url, headers=merged, json=json_body)
        else:
            res = await client.get(url, headers=merged)
    store_cookies(res.headers)
    return res


async def _raw_get(url, headers=None, timeout=EXTRACT_TIMEOUT):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        res = await client.get(url, headers=headers or {})
    store_cookies(res.headers)
    return res


async def _raw_post(url, payload, headers=None, timeout=EXTRACT_TIMEOUT):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        res = await client.post(url, headers=headers or {}, json=payload)
    store_cookies(res.headers)
    return res


def find_balanced_block(text: str, start: int) -> int:
    depth = 0
    seen = False
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
            seen = True
        elif ch == "}":
            depth -= 1
            if seen and depth == 0:
                return i + 1
    return -1


def normalize_crypto_config(out):
    if not out or not out.get("buildId"):
        return None
    parts = out.get("maskParts")
    if not isinstance(parts, list) or len(parts) < 4:
        return None
    return {"scheme": "legacy", "buildId": str(out["buildId"]),
            "maskParts": [str(p) for p in parts[:4]]}


def find_statement_end(text: str, start: int) -> int:
    parens = brackets = braces = 0
    quote_ch = ""
    i = start
    while i < len(text):
        ch = text[i]
        if quote_ch:
            if ch == "\\":
                i += 1
            elif ch == quote_ch:
                quote_ch = ""
        elif ch == '"' or ch == "'" or ch == chr(96):
            quote_ch = ch
        elif ch == "(":
            parens += 1
        elif ch == ")":
            parens -= 1
        elif ch == "[":
            brackets += 1
        elif ch == "]":
            brackets -= 1
        elif ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        elif ch == ";" and not parens and not brackets and not braces:
            return i
        i += 1
    return -1


def _split_top(text: str, sep: str = ",") -> list:
    out = []
    start = 0
    parens = brackets = braces = 0
    quote_ch = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote_ch:
            if ch == "\\":
                i += 1
            elif ch == quote_ch:
                quote_ch = ""
        elif ch == '"' or ch == "'" or ch == chr(96):
            quote_ch = ch
        elif ch == "(":
            parens += 1
        elif ch == ")":
            parens -= 1
        elif ch == "[":
            brackets += 1
        elif ch == "]":
            brackets -= 1
        elif ch == "{":
            braces += 1
        elif ch == "}":
            braces -= 1
        elif ch == sep and not parens and not brackets and not braces:
            out.append(text[start:i])
            start = i + 1
        i += 1
    out.append(text[start:])
    return out


def split_top_level(text: str) -> list:
    return _split_top(text or "", ",")


def declaration_statement_at(text: str, index: int):
    start = max(text.rfind("const ", 0, index), text.rfind("let ", 0, index),
                text.rfind("var ", 0, index))
    if start < 0:
        return None
    end = find_statement_end(text, start)
    if end < 0 or index > end:
        return None
    keyword = re.match(r"(?:const|let|var)\s+", text[start:])
    if not keyword:
        return None
    entries = []
    for value in split_top_level(text[start + len(keyword.group(0)):end]):
        entry = re.match(r"^\s*(" + IDENT + r")\s*=\s*([\s\S]+)$", value)
        if entry:
            entries.append({"name": entry.group(1), "expression": entry.group(2),
                            "start": start, "end": end})
    return {"start": start, "end": end, "entries": entries}


def template_dependencies(expression: str) -> list:
    return [m.group(1) for m in
            re.finditer(r"\$\{\s*(" + IDENT + r")\b[^}]*\}", expression or "")]


def template_declaration(chunk: str, name: str, before=None):
    if before is None:
        before = len(chunk)
    pattern = re.compile(r"(?<![A-Za-z0-9_$])" + re.escape(name) + r"\s*=")
    fallback = None
    for match in pattern.finditer(chunk or ""):
        statement = declaration_statement_at(chunk, match.start())
        if not statement:
            continue
        entry = next((e for e in statement["entries"] if e["name"] == name), None)
        if not entry:
            continue
        if fallback is None:
            fallback = entry
        if entry["start"] < before:
            fallback = entry
    return fallback


def valid_episode_query(query) -> bool:
    if not isinstance(query, str):
        return False
    if re.search("[\ud800-\udfff]", query):
        return False
    if "${" in query:
        return False
    if not re.search(r"\bquery\b", query):
        return False
    if not re.search(r"\bepisode\s*\(\s*showId\s*:\s*\$showId\s+translationType"
                     r"\s*:\s*\$translationType\s+episodeString\s*:\s*\$episodeString"
                     r"\s*\)", query):
        return False
    return True


def _unescape_js_string(token: str) -> str:
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        try:
            return json.loads(token)
        except Exception:
            pass
    body = token[1:-1] if len(token) >= 2 else token
    body = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), body)
    body = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), body)
    simple = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
    return re.sub(r"\\(.)", lambda m: simple.get(m.group(1), m.group(1)), body)


def _resolve_def(name: str, defs: dict, stack: set) -> str:
    if name in stack:
        raise ValueError("cyclic template ref: " + name)
    if name not in defs:
        raise ValueError("unknown template ref: " + name)
    stack.add(name)
    try:
        expr = (defs[name] or "").strip()
        m = (re.fullmatch(r"\(\s*\)\s*=>\s*([\s\S]+)", expr)
             or re.fullmatch(r"\(" + IDENT + r"\s*\)\s*=>\s*([\s\S]+)", expr))
        if m:
            return _eval_str_expr(m.group(1), defs, stack)
        m = (re.fullmatch(r"\([^)]*\)\s*=>\s*\{\s*return\s+([\s\S]+?);?\s*\}", expr)
             or re.fullmatch(r"function\s*\([^)]*\)\s*\{\s*return\s+([\s\S]+?);?\s*\}", expr))
        if m:
            return _eval_str_expr(m.group(1), defs, stack)
        return _eval_str_expr(expr, defs, stack)
    finally:
        stack.discard(name)


def _eval_str_expr(expr: str, defs: dict, stack: set) -> str:
    e = (expr or "").strip()
    m = re.fullmatch(r"(" + IDENT + r")\s*\(\s*\)", e)
    if m:
        return _resolve_def(m.group(1), defs, stack)
    if re.fullmatch(IDENT, e):
        return _resolve_def(e, defs, stack)
    if len(e) >= 2 and e[0] in ('"', "'", chr(96)) and e[-1] == e[0]:
        if e[0] == chr(96):
            def _rep(mm):
                inner = (mm.group(1) or "").strip()
                im = (re.fullmatch(r"(" + IDENT + r")\s*\(\s*\)", inner)
                      or re.fullmatch(IDENT, inner))
                if not im:
                    raise ValueError("complex template expression")
                return _resolve_def(im.group(1), defs, stack)
            return re.sub(r"\$\{([^{}]*)\}", _rep, e[1:-1])
        return _unescape_js_string(e)
    parts = _split_top(e, "+")
    if len(parts) > 1:
        return "".join(_eval_str_expr(p, defs, stack) for p in parts)
    raise ValueError("cannot evaluate expression: " + e[:80])


def eval_episode_query_chunk(chunk: str):
    operation = (r"\bepisode\s*\(\s*showId\s*:\s*\$showId\s*translationType"
                 r"\s*:\s*\$translationType\s*episodeString\s*:\s*\$episodeString\s*\)")
    for match in re.finditer(operation, chunk or ""):
        statement = declaration_statement_at(chunk, match.start())
        if not statement:
            continue
        candidate = next((e for e in statement["entries"]
                          if re.search(operation, e["expression"])), None)
        if not candidate:
            continue
        defs = {}
        resolving = set()

        def resolve(name: str, before: int) -> None:
            if name in defs or name in resolving:
                return
            entry = template_declaration(chunk, name, before)
            if not entry:
                return
            resolving.add(name)
            for dep in template_dependencies(entry["expression"]):
                resolve(dep, entry["start"])
            resolving.discard(name)
            defs[name] = entry["expression"]

        resolve(candidate["name"], candidate["start"] + 1)
        try:
            query = _resolve_def(candidate["name"], defs, set())
            if valid_episode_query(query):
                return query
        except Exception:
            pass
    return None


def _extract_mask_array(text: str):
    found = None
    for m in re.finditer(r'''\[((?:\s*"[A-Za-z0-9+/=]{8,}"\s*,?)+)\s*\]''', text or ""):
        strings = [s[1:-1] for s in re.findall(r'''"[A-Za-z0-9+/=]{8,}"''', m.group(1))]
        if len(strings) >= 4:
            found = strings[:4]
    return found


def _extract_build_id(text: str):
    m = re.search(r'''\?"([0-9]+)"\s*:\s*""''', text or "")
    return m.group(1) if m else None


def eval_fragment_crypto_chunk(chunk: str):
    for match in re.finditer(r"\bsaltMul\s*:", chunk or ""):
        window = chunk[max(0, match.start() - 4000):match.start() + 4000]
        nums = {}
        ok = True
        for key in ("saltMul", "saltAdd", "fragMul", "fragAdd"):
            m = re.search(r"\b" + key + r"\s*:\s*(-?[0-9]+(?:\.[0-9]+)?)", window)
            if not m:
                ok = False
                break
            try:
                nums[key] = float(m.group(1))
            except ValueError:
                ok = False
                break
        if not ok:
            continue
        if any(v != v or v in (float("inf"), float("-inf")) for v in nums.values()):
            continue
        boot = re.search(r'''\bbootPrefix\s*:\s*"([^"]*)"''', window)
        join = re.search(r'''\bjoin\s*:\s*"([^"]*)"''', window)
        parts_m = re.search(r"\bparts\s*:\s*\[([^\]]*)\]", window)
        if not boot or not join or not parts_m:
            continue
        parts = re.findall(r'''"([^"]+)"''', parts_m.group(1))
        if not parts:
            continue
        mask = _extract_mask_array(chunk[:match.start()])
        if not mask:
            continue
        build_id = _extract_build_id(chunk[:match.start()]) or _extract_build_id(window)
        if not build_id:
            continue
        omit = bool(re.search(r"\bomitEmptyLane\s*:\s*(!0|true)", window))
        return {"scheme": "fragments", "buildId": str(build_id),
                "maskParts": [str(p) for p in mask],
                "saltMul": nums["saltMul"], "saltAdd": nums["saltAdd"],
                "fragMul": nums["fragMul"], "fragAdd": nums["fragAdd"],
                "bootPrefix": boot.group(1), "join": join.group(1),
                "parts": [str(p) for p in parts], "omitEmptyLane": omit}
    return None


def _eval_legacy_shape(chunk: str):
    m = re.search(r'''const\s+''' + IDENT + r'''\s*=[^;]{0,220}?"([0-9]+)"\s*:\s*"",\s*'''
                  r'''(''' + IDENT + r''')=\[((?:"[^"]*",?\s*)+)\]''', chunk or "", re.DOTALL)
    if not m:
        return None
    strings = re.findall(r'''"([^"]*)"''', m.group(3))
    return normalize_crypto_config({"buildId": m.group(1), "maskParts": strings})


def eval_old_crypto_chunk(chunk: str):
    m = re.search(r'''const\s+''' + IDENT + r'''\s*=[^;]{0,180}?"\d+":""''', chunk or "")
    if not m:
        return None
    return _eval_legacy_shape(chunk[m.start():m.start() + 20000])


def eval_modern_crypto_chunk(chunk: str):
    m = re.search(r'''const\s+''' + IDENT + r'''\s*=[^;]{0,220}?"\d+":""''', chunk or "")
    if not m:
        return None
    return _eval_legacy_shape(chunk[m.start():m.start() + 20000])

_JS_IDENT = r"[A-Za-z_$][\w$]*"


def _js_number(tok: str):
    tok = tok.strip()
    try:
        if tok[:2].lower() == "0x":
            return int(tok, 16)
        if "." in tok or "e" in tok.lower():
            return float(tok)
        return int(tok)
    except ValueError:
        raise ValueError("bad number: " + tok[:40])


def _js_parse_int(value) -> float:
    m = re.match(r"\s*[+-]?(?:0[xX][0-9a-fA-F]+|\d+)", str(value))
    if not m:
        return float("nan")
    tok = m.group(0).strip()
    neg = tok.startswith("-")
    tok = tok.lstrip("+-")
    try:
        num = int(tok, 16) if tok[:2].lower() == "0x" else int(tok)
    except ValueError:
        return float("nan")
    return -num if neg else num


def _tok_js(expr: str) -> list:
    toks, i, n = [], 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and expr[i + 1].isdigit()):
            m = re.match(r"0[xX][0-9a-fA-F]+|\d+\.?\d*", expr[i:])
            toks.append(("num", m.group(0)))
            i += len(m.group(0))
            continue
        if ch in ("'", '"'):
            j = i + 1
            buf = []
            while j < n:
                c = expr[j]
                if c == "\\":
                    buf.append(expr[j:j + 2])
                    j += 2
                    continue
                if c == ch:
                    break
                buf.append(c)
                j += 1
            toks.append(("str", ch + "".join(buf) + ch))
            i = j + 1
            continue
        if ch.isalpha() or ch in ("_", "$"):
            m = re.match(r"[A-Za-z_$][\w$]*", expr[i:])
            name = m.group(0)
            i += len(name)
            while i < n and expr[i] == ".":
                m2 = re.match(r"\.[A-Za-z_$][\w$]*", expr[i:])
                if not m2:
                    break
                name += m2.group(0)
                i += len(m2.group(0))
            toks.append(("ident", name))
            continue
        if ch in "+-*/,(){}:." :
            toks.append(("op", ch))
            i += 1
            continue
        raise ValueError("bad char in expression: " + ch)
    return toks


class _JsParser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0

    def peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else (None, None)

    def next(self):
        t = self.peek()
        self.pos += 1
        return t

    def parse(self):
        node = self.parse_expr()
        if self.pos != len(self.toks):
            raise ValueError("trailing tokens")
        return node

    def parse_expr(self):
        node = self.parse_term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.next()[1]
            node = ("bin", op, node, self.parse_term())
        return node

    def parse_term(self):
        node = self.parse_factor()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.next()[1]
            node = ("bin", op, node, self.parse_factor())
        return node

    def parse_factor(self):
        kind, val = self.peek()
        if (kind, val) in (("op", "-"), ("op", "+")):
            self.next()
            return ("un", val, self.parse_factor())
        node = self.parse_primary()
        while self.peek() == ("op", "."):
            self.next()
            k, v = self.next()
            if k != "ident" or "." in v:
                raise ValueError("bad member access")
            node = ("member", node, v)
        return node

    def parse_primary(self):
        kind, val = self.peek()
        if (kind, val) == ("op", "{"):
            self.next()
            pairs = []
            if self.peek() != ("op", "}"):
                while True:
                    k, v = self.next()
                    if k not in ("ident", "str", "num"):
                        raise ValueError("bad object key")
                    key = v[1:-1] if k == "str" else v
                    if self.next() != ("op", ":"):
                        raise ValueError("missing :")
                    pairs.append((key, self.parse_expr()))
                    if self.peek() == ("op", ","):
                        self.next()
                        continue
                    break
            if self.next() != ("op", "}"):
                raise ValueError("missing }")
            return ("obj", pairs)
        if (kind, val) == ("op", "("):
            self.next()
            node = self.parse_expr()
            if self.next() != ("op", ")"):
                raise ValueError("missing )")
            return node
        if kind == "num":
            self.next()
            return ("num", val)
        if kind == "str":
            self.next()
            return ("str", val)
        if kind == "ident":
            self.next()
            if self.peek() == ("op", "("):
                self.next()
                args = []
                if self.peek() != ("op", ")"):
                    args.append(self.parse_expr())
                    while self.peek() == ("op", ","):
                        self.next()
                        args.append(self.parse_expr())
                if self.next() != ("op", ")"):
                    raise ValueError("missing call )")
                return ("call", val, args)
            return ("var", val)
        raise ValueError("unexpected token")


def _js_eval(node, env):
    kind = node[0]
    if kind == "num":
        return _js_number(node[1])
    if kind == "str":
        return _unescape_js_string(node[1])
    if kind == "var":
        cur = env
        for part in node[1].split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                raise ValueError("unknown var: " + node[1])
        if callable(cur):
            raise ValueError("bare callable ref: " + node[1])
        return cur
    if kind == "obj":
        return {k: _js_eval(v, env) for k, v in node[1]}
    if kind == "member":
        obj = _js_eval(node[1], env)
        if isinstance(obj, dict) and node[2] in obj:
            return obj[node[2]]
        raise ValueError("bad member: " + node[2])
    if kind == "un":
        val = _js_eval(node[2], env)
        return -val if node[1] == "-" else val
    if kind == "bin":
        left = _js_eval(node[2], env)
        right = _js_eval(node[3], env)
        if node[1] == "+" and (isinstance(left, str) or isinstance(right, str)):
            return str(left) + str(right)
        return {"+": left + right, "-": left - right,
                "*": left * right, "/": left / right}[node[1]]
    if kind == "call":
        name, args = node[1], [_js_eval(a, env) for a in node[2]]
        if name == "parseInt":
            return _js_parse_int(args[0] if args else "")
        fns = env.get("__calls__", {})
        if name not in fns:
            raise ValueError("unknown call: " + name)
        return fns[name](*args)
    raise ValueError("bad node")


def _js_eval_str(expr: str, env: dict):
    return _js_eval(_JsParser(_tok_js(expr)).parse(), env)


def _scan_balanced(text: str, i: int) -> int:
    n = len(text)
    while i < n and text[i] not in "([{":
        i += 1
    stack = [text[i]]
    pairs = {")": "(", "]": "[", "}": "{"}
    i += 1
    in_str, quote, esc = False, "", False
    while i < n and stack:
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        elif ch in ("'", '"'):
            in_str, quote = True, ch
        elif ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if stack and stack[-1] == pairs[ch]:
                stack.pop()
            else:
                raise ValueError("unbalanced")
        i += 1
    return i


def _parse_js_array_literal(src: str, env: dict | None = None):
    src = src.strip()
    if not (src.startswith("[") and src.endswith("]")):
        raise ValueError("not an array")
    env = env if env is not None else {}
    return [_eval_js_value(p, env) for p in _split_top(src[1:-1]) if p.strip()]


def _eval_js_value(src: str, env: dict):
    src = src.strip()
    if src in ("!0", "true"):
        return True
    if src in ("!1", "false"):
        return False
    if src == "null":
        return None
    if src.startswith("["):
        return _parse_js_array_literal(src, env)
    if src.startswith("{"):
        if not src.endswith("}"):
            raise ValueError("bad object")
        out = {}
        for part in _split_top(src[1:-1]):
            if not part.strip():
                continue
            km = re.match(r"""\s*(?:"([^"]*)"|'([^']*)'|([A-Za-z_$][\w$]*))\s*:""", part)
            if not km:
                raise ValueError("bad object key")
            key = km.group(1) or km.group(2) or km.group(3)
            out[key] = _eval_js_value(part[km.end():], env)
        return out
    return _js_eval_str(src, env)


def _parse_map_literal(body: str) -> dict:
    out = {}
    for part in _split_top(body):
        m = re.match(r"\s*([A-Za-z_$][\w$]*)\s*:\s*(.+?)\s*$", part, re.DOTALL)
        if not m:
            continue
        try:
            out[m.group(1)] = _js_eval_str(m.group(2), {})
        except Exception:
            continue
    return out


def _rotate_live_table(chunk, tbl_name, ua_name, ua_off, header, check_src, target_src):
    tm = re.search(r"function\s+" + re.escape(tbl_name) + r"\(\)\s*\{\s*const\s+e\s*=\s*\[", chunk)
    if not tm:
        return None
    arr_end = _scan_balanced(chunk, tm.end() - 1)
    table = _parse_js_array_literal(chunk[tm.end() - 1:arr_end])
    if not table or not all(isinstance(s, str) for s in table):
        return None
    live = list(table)

    def _ua(idx):
        i = int(idx) - ua_off
        if i < 0 or i >= len(live):
            raise IndexError("table miss")
        return live[i]

    maps: dict = {}
    for mm in re.finditer(r"(?:^|[,;{\s]|(?:const|let|var)\s+)([a-zA-Z])\s*=\s*\{([^}]*)\}", header):
        parsed = _parse_map_literal(mm.group(2))
        if parsed:
            maps[mm.group(1)] = parsed
    inner: dict = {}
    for im in re.finditer(
            r"function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{\s*return\s+" + re.escape(ua_name) + r"\(([^;]+?)\)",
            header):
        fname, pnames, body = im.group(1), im.group(2), im.group(3)
        if fname == ua_name:
            continue
        params = tuple(p.strip() for p in pnames.split(",") if p.strip())

        def _fn(*args, _b=body, _p=params):
            local = dict(zip(_p, args))
            local.update(maps)
            local["__calls__"] = {ua_name: _ua}
            return _ua(_js_eval_str(_b, local))

        inner[fname] = _fn
    try:
        target = _js_eval_str(target_src, {})
        check_node = _JsParser(_tok_js(check_src)).parse()
    except Exception:
        return None

    def _run_check():
        env = dict(inner)
        env["__calls__"] = dict(inner)
        env.update(maps)
        return _js_eval(check_node, env)

    for _ in range(30000):
        try:
            if _run_check() == target:
                break
        except Exception:
            pass
        live.append(live.pop(0))
    else:
        return None
    return {"live": live, "ua": _ua, "maps": maps}


def _emulate_wrappers(chunk):
    _nm = r"[A-Za-z_$][\w$]*"
    ua_pat = (r"function\s+(" + _nm + r")\s*\(\s*e\s*,\s*t\s*\)\s*\{\s*return\s+e\s*=\s*e\s*-\s*(\d+)"
                r"\s*,\s*(" + _nm + r")\(\)\[e\]")
    iife_end = re.compile(
        r"===t\)break;(a|n)\.push\(\1\.shift\(\)\)\}catch\{\1\.push\(\1\.shift\(\)\)\}\}\)\("
        r"(" + _nm + r")\s*,\s*([^;]{1,400})\);"
    )
    blocks = []
    for fm in re.finditer(r"for\(;;\)try\{if\(", chunk or ""):
        p = fm.end()
        q = chunk.find("===t)break;", p)
        if q == -1 or q - p < 10 or q - p > 4000:
            continue
        check_src = chunk[p:q]
        if "for(;;)" in check_src:
            continue
        em = iife_end.match(chunk, q)
        if not em:
            continue
        arrvar, tbl, target_src = em.group(1), em.group(2), em.group(3)
        anchor = chunk.rfind(arrvar + "=e();", 0, fm.start())
        if anchor == -1:
            anchor = chunk.rfind(arrvar + " = e();", 0, fm.start())
        if anchor == -1:
            continue
        iife_start = chunk.rfind("(function(", 0, anchor)
        header = chunk[iife_start:fm.start()] if iife_start != -1 else ""
        if (arrvar + "=e();") not in header and (arrvar + " = e();") not in header:
            continue
        blocks.append({"table": tbl, "check": check_src,
                       "target": target_src, "header": header})
    direct: dict = {}
    for um in re.finditer(ua_pat, chunk or ""):
        direct.setdefault(um.group(1), (int(um.group(2)), um.group(3)))
    runtimes: dict = {}
    maps_variants: list = [{}]
    for tbl in {b["table"] for b in blocks} | {t for _, t in direct.values()}:
        specs = [b for b in blocks if b["table"] == tbl]
        uas = [(n, o) for n, (o, t) in direct.items() if t == tbl]
        if not specs:
            for (n, o) in uas:
                tm = re.search(r"function\s+" + re.escape(tbl) + r"\(\)\s*\{\s*const\s+e\s*=\s*\[", chunk)
                if not tm:
                    continue
                try:
                    arr_end = _scan_balanced(chunk, tm.end() - 1)
                    table = _parse_js_array_literal(chunk[tm.end() - 1:arr_end])
                except Exception:
                    continue
                if not table:
                    continue
                live = list(table)

                def _ua(idx, _live=live, _off=o):
                    i = int(idx) - _off
                    if i < 0 or i >= len(_live):
                        raise IndexError("table miss")
                    return _live[i]

                runtimes[n] = _ua
            continue
        done = False
        for spec in specs:
            for (n, o) in uas:
                try:
                    rt = _rotate_live_table(chunk, tbl, n, o, spec["header"], spec["check"], spec["target"])
                except Exception:
                    continue
                if not rt:
                    continue
                runtimes[n] = rt["ua"]
                if rt["maps"]:
                    maps_variants.append(rt["maps"])
                done = True
                break
            if done:
                break
    wrappers: dict = dict(runtimes)
    pending = {}
    _nm2 = r"[A-Za-z_$][\w$]*"
    for wm in re.finditer(
            r"function\s+(" + _nm2 + r")\s*\(([^)]*)\)\s*\{\s*return\s+(" + _nm2 + r")\(([^;]+?)\)",
            chunk or ""):
        fname, pnames, acc, body = wm.group(1), wm.group(2), wm.group(3), wm.group(4)
        if fname in wrappers:
            continue
        params = tuple(p.strip() for p in pnames.split(",") if p.strip())
        if not params:
            continue
        pending[fname] = (params, acc, body)

    for _ in range(12):
        progressed = False
        for wname, (params, acc, body) in list(pending.items()):
            if acc not in wrappers:
                continue

            def _mk(_b=body, _p=params, _a=acc):
                def _f(*args):
                    local = dict(zip(_p, args))
                    calls = dict(wrappers)
                    local["__calls__"] = calls
                    return calls[_a](_js_eval_str(_b, local))
                return _f

            wrappers[wname] = _mk()
            del pending[wname]
            progressed = True
        if not progressed:
            break
    return wrappers, maps_variants


def _emulated_fragment_config(chunk: str):
    try:
        wrappers, maps_variants = _emulate_wrappers(chunk)
    except Exception:
        return None
    if not wrappers:
        return None

    def _stmt_at(pos):
        cstart = chunk.rfind("const ", 0, pos)
        if cstart == -1:
            cstart = chunk.rfind("let ", 0, pos)
        if cstart == -1:
            cstart = chunk.rfind("var ", 0, pos)
        if cstart == -1:
            return None
        semi, depth = cstart, 0
        in_str, quote, esc = False, "", False
        n = len(chunk)
        while semi < n:
            ch = chunk[semi]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == quote:
                    in_str = False
            elif ch in ("'", '"'):
                in_str, quote = True, ch
            elif ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ";" and depth == 0:
                break
            semi += 1
        decls = []
        for part in _split_top(chunk[cstart + 6:semi]):
            dm = re.match(r"\s*([A-Za-z_$][\w$]*)\s*=\s*([\s\S]+)$", part)
            if dm:
                decls.append((dm.group(1), dm.group(2).strip()))
        return decls

    statements = []
    for sm in re.finditer(r"\bsaltMul\s*:", chunk):
        decls = _stmt_at(sm.start())
        if not decls:
            continue
        cfg_idx = next((i for i, (_, e) in enumerate(decls)
                        if e.startswith("{") and "saltMul" in e and "fragMul" in e), None)
        if cfg_idx is None or cfg_idx < 1:
            continue
        mask_idx = next((i for i in range(cfg_idx - 1, -1, -1)
                         if decls[i][1].startswith("[")), None)
        if mask_idx is None:
            continue
        statements.append((decls[mask_idx][1], decls[cfg_idx][1]))
    for maps in maps_variants:
        calls = dict(wrappers)

        def _venv(_m=maps, _c=calls):
            env = dict(_m)
            env["__calls__"] = dict(_c)
            return env

        for mask_src, cfg_src in statements:
            try:
                mv = _eval_js_value(mask_src, _venv())
                cv = _eval_js_value(cfg_src, _venv())
            except Exception:
                continue
            if (isinstance(mv, list) and len(mv) >= 4
                    and isinstance(cv, dict)
                    and all(isinstance(cv.get(k), (int, float))
                            for k in ("saltMul", "saltAdd", "fragMul", "fragAdd"))
                    and isinstance(cv.get("parts"), list) and cv["parts"]
                    and isinstance(cv.get("bootPrefix"), str)
                    and isinstance(cv.get("join"), str)):
                try:
                    for p in mv[:4]:
                        if len(base64.b64decode(str(p))) < 8:
                            raise ValueError("short mask")
                except Exception:
                    continue
            else:
                continue
            build_id = None
            bm = re.search(r'\?"(\d+)":""', chunk)
            if bm:
                build_id = bm.group(1)
            if not build_id:
                names: list = []
                for m3 in re.finditer(r"buildId\s*:\s*\w+\s*=\s*([A-Za-z_$][\w$]*)", chunk):
                    names.append(m3.group(1))
                for m3 in re.finditer(r"buildId\s*:\s*([A-Za-z_$][\w$]*)\|\|", chunk):
                    names.append(m3.group(1))
                names += ["sd", "Fr"]
                seen = set()
                for var in names:
                    if var in seen:
                        continue
                    seen.add(var)
                    m4 = re.search(r"(?<![\w$])" + re.escape(var) + r"\s*=\s*([A-Za-z_$][\w$]*\([^()]*\))", chunk)
                    if not m4:
                        continue
                    try:
                        bid = _js_eval_str(m4.group(1), _venv())
                        if bid:
                            build_id = str(bid)
                            break
                    except Exception:
                        continue
            if not build_id:
                continue
            return {
                "scheme": "fragments",
                "buildId": build_id,
                "maskParts": [str(p) for p in mv[:4]],
                "saltMul": cv["saltMul"],
                "saltAdd": cv["saltAdd"],
                "fragMul": cv["fragMul"],
                "fragAdd": cv["fragAdd"],
                "bootPrefix": cv["bootPrefix"],
                "join": cv["join"],
                "parts": [str(p) for p in cv["parts"]],
                "omitEmptyLane": bool(cv.get("omitEmptyLane")),
            }
    return None


def eval_crypto_chunk(chunk: str):
    try:
        config = _emulated_fragment_config(chunk)
        if config:
            return config
    except Exception as e:
        print(f"[mkissa] emulated discovery failed: {str(e)[:150]}")
    try:
        config = eval_fragment_crypto_chunk(chunk)
        if config:
            return config
    except Exception:
        pass
    try:
        found = eval_modern_crypto_chunk(chunk)
        if found:
            return found
    except Exception:
        pass
    try:
        return eval_old_crypto_chunk(chunk)
    except Exception:
        return None


def app_entry_url(html: str):
    m = re.search(r'''(?:import\(|src=)"([^"]+/_app/immutable/entry/app\.[^"]+\.js)"''', html or "")
    return urljoin(REFERER, m.group(1)) if m else None


def _chunk_imports(item_url: str, text: str) -> list:
    urls = []
    for m in re.finditer(r'''(?:import\(|from\s*)"([^"]+\.js)"''', text or ""):
        value = m.group(1)
        if value.startswith(".") or value.startswith("/"):
            nxt = urljoin(item_url, value)
            if nxt not in urls:
                urls.append(nxt)
    for m in re.finditer(r'''"(\.\./(?:chunks|nodes)/[^"\n]+\.js)"''', text or ""):
        nxt = urljoin(item_url, m.group(1))
        if nxt not in urls:
            urls.append(nxt)
    return urls


async def _fetch_chunk(url: str):
    try:
        headers = {"Accept": "application/javascript,*/*", "Referer": f"{REFERER}/"}
        cookie = cookie_header()
        if cookie:
            headers["Cookie"] = cookie
        return await fetch_text(url, headers)
    except Exception:
        return None


async def discover_crypto_config(force: bool = False) -> dict:
    global _crypto_config
    try:
        entry_url = f"{REFERER}/"
        if force:
            entry_url += f"?_mkissa={int(time.time() * 1000)}"
        html = await _session_get_text(entry_url, {
            "Accept": "text/html,*/*", "Cache-Control": "no-cache", "Pragma": "no-cache"})
        app_url = app_entry_url(html)
        if not app_url:
            raise RuntimeError("MKissa app entry not found")
        if not force and _crypto_config and _crypto_config.get("app_url") == app_url:
            return _crypto_config
        first = await _fetch_chunk(app_url)
        if first is None:
            raise RuntimeError("MKissa app entry fetch failed")
        queue = [app_url]
        seen = set()
        cached = {app_url: first}
        sem = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
        while queue and len(seen) < DISCOVERY_LIMIT:
            batch = []
            while queue and len(batch) < DISCOVERY_CONCURRENCY:
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                batch.append(url)

            async def _one(url: str):
                if url in cached:
                    return {"url": url, "text": cached[url]}
                async with sem:
                    text = await _fetch_chunk(url)
                return {"url": url, "text": text} if text is not None else None

            chunks = await asyncio.gather(*[_one(u) for u in batch])
            for item in chunks:
                if not item:
                    continue
                for nxt in _chunk_imports(item["url"], item["text"]):
                    if nxt not in seen:
                        queue.append(nxt)
                if not re.search(r"client-crypto|x-aa-boot|aaReq|partB", item["text"]):
                    continue
                config = eval_crypto_chunk(item["text"])
                if config:
                    _crypto_config = {**config, "app_url": app_url,
                                      "source_url": item["url"]}
                    return _crypto_config
        raise RuntimeError("MKissa crypto chunk not found")
    except Exception:
        _crypto_config = None
        raise


async def discover_episode_query(force: bool = False) -> str:
    global _episode_query_cache
    config = await discover_crypto_config(force)
    if (not force and _episode_query_cache
            and _episode_query_cache.get("app_url") == config.get("app_url")
            and _episode_query_cache.get("buildId") == config.get("buildId")):
        return _episode_query_cache["query"]

    def inspect(text):
        global _episode_query_cache
        query = eval_episode_query_chunk(text or "")
        if query:
            _episode_query_cache = {"app_url": config.get("app_url"),
                                    "buildId": config.get("buildId"), "query": query}
            return query
        return None

    if config.get("source_url"):
        try:
            found = inspect(await _fetch_chunk(config["source_url"]))
            if found:
                return found
        except Exception:
            pass
    try:
        html = await _session_get_text(f"{REFERER}/", {
            "Accept": "text/html,*/*", "Cache-Control": "no-cache", "Pragma": "no-cache"})
        app_url = app_entry_url(html)
    except Exception:
        app_url = None
    if not app_url:
        return episode_query()
    queue = [app_url]
    seen = set()
    sem = asyncio.Semaphore(DISCOVERY_CONCURRENCY)
    while queue and len(seen) < DISCOVERY_LIMIT:
        batch = []
        while queue and len(batch) < DISCOVERY_CONCURRENCY:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            batch.append(url)

        async def _one(url: str):
            async with sem:
                text = await _fetch_chunk(url)
            return {"url": url, "text": text} if text is not None else None

        chunks = await asyncio.gather(*[_one(u) for u in batch])
        for item in chunks:
            if not item:
                continue
            found = inspect(item["text"])
            if found:
                return found
            for nxt in _chunk_imports(item["url"], item["text"]):
                if nxt not in seen:
                    queue.append(nxt)
    return episode_query()


def build_mask_seed(build_id: str) -> bytes:
    name = str(build_id or "")
    out = bytearray(32)
    for i in range(32):
        code = ord(name[i % len(name)]) if name else 0
        out[i] = (code ^ ((i * 17 + 31) & 255)) & 255
    return bytes(out)


def build_mask(config: dict) -> bytes:
    build_id = str(config.get("buildId") or "")
    mask_parts = config.get("maskParts") or []
    if config.get("scheme") == "fragments":
        salt_mul = config.get("saltMul", 0)
        salt_add = config.get("saltAdd", 0)
        frag_mul = config.get("fragMul", 0)
        frag_add = config.get("fragAdd", 0)
        salt = bytearray(32)
        for i in range(32):
            code = ord(build_id[i % len(build_id)]) if build_id else 0
            salt[i] = (code ^ (int(i * salt_mul + salt_add) & 255)) & 255
        out = bytearray(32)
        for i in range(min(4, len(mask_parts))):
            part = base64.b64decode(str(mask_parts[i]))
            offset = i * 8
            for j in range(8):
                out[offset + j] = (part[j] ^ salt[offset + j]
                                   ^ (int(i * frag_mul + j * frag_add) & 255)) & 255
        return bytes(out)
    seed = build_mask_seed(build_id)
    out = bytearray(32)
    for i in range(min(4, len(mask_parts))):
        part = base64.b64decode(str(mask_parts[i]))
        offset = i * 8
        for j in range(8):
            out[offset + j] = ((part[j] ^ seed[offset + j])
                               ^ ((i * 41 + j * 7) & 255)) & 255
    return bytes(out)


def current_epochs(now_ms=None) -> list:
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    epoch = now // BOOT_EPOCH_MS
    prev = epoch - 1 if (now - epoch * BOOT_EPOCH_MS < BOOT_GRACE_MS and epoch > 0) else epoch
    return list(dict.fromkeys([prev, epoch]))


def make_boot_token(config: dict, epoch, lane: str = CONTENT_LANE) -> str:
    mask = build_mask(config)
    prefix = config.get("bootPrefix") or "aa-boot:"
    bid = str(config.get("buildId"))
    boot_key = hmac_bytes(mask, f"{prefix}{bid}")
    if config.get("scheme") != "fragments":
        return hmac_bytes(boot_key, f"{bid}:{KEY_GROUP}:{REFERER_HOST}:{epoch}:{lane}").hex()
    fields = {"buildId": bid, "group": KEY_GROUP,
              "host": REFERER_HOST, "epoch": str(epoch), "lane": str(lane or "")}
    parts = list(config.get("parts") or [])
    if config.get("omitEmptyLane") and not fields["lane"]:
        parts = [p for p in parts if p != "lane"]
    joiner = str(config.get("join", ""))
    return hmac_bytes(boot_key, joiner.join(fields.get(p, "") for p in parts)).hex()


def is_unknown_build_id(raw: str) -> bool:
    try:
        if json.loads(raw or "").get("error") == "unknown_build_id":
            return True
    except Exception:
        pass
    return bool(re.search(r"unknown_build_id", raw or "", re.IGNORECASE))


async def fetch_bootstrap(lane: str = CONTENT_LANE, force: bool = False) -> dict:
    global _crypto_config, _episode_query_cache
    last_error = None
    for refresh in range(2):
        config = await discover_crypto_config(force or refresh > 0)
        retry_fresh = False
        for epoch in current_epochs():
            res = await _api_request(
                "GET",
                f"{API}/client-crypto/v1/bootstrap?buildId={quote(str(config['buildId']), safe="'")}"
                f"&k={quote(str(lane), safe="'")}",
                {"Referer": f"{REFERER}/", "Origin": REFERER,
                 "x-build-id": config["buildId"],
                 "x-aa-boot": make_boot_token(config, epoch, lane)})
            raw = res.text
            if res.status_code != 200:
                last_error = RuntimeError(f"Bootstrap {res.status_code}: {raw[:180]}")
                if is_unknown_build_id(raw):
                    _crypto_config = None
                    _episode_query_cache = None
                    retry_fresh = True
                    break
                continue
            try:
                data = json.loads(raw)
            except Exception:
                last_error = RuntimeError("Bootstrap invalid JSON")
                continue
            if not data.get("partB"):
                last_error = RuntimeError("Bootstrap missing partB")
                continue
            return {**data, **config, "lane": lane, "buildId": config["buildId"]}
        if not retry_fresh:
            break
    raise last_error or RuntimeError("MKissa bootstrap failed")


def derive_lane_key(part_b: str, config: dict) -> bytes:
    encrypted = base64.b64decode(part_b)
    mask = build_mask(config)
    key = bytearray(32)
    for i in range(32):
        key[i] = encrypted[i] ^ mask[i % len(mask)]
    return bytes(key)


async def get_lane_key(lane: str = CONTENT_LANE, force: bool = False) -> dict:
    boot = await fetch_bootstrap(lane, force)
    return {"key": derive_lane_key(boot["partB"], boot),
            "epoch": boot.get("epoch"), "buildId": boot.get("buildId")}


def _gf_mul(a: int, b: int) -> int:
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _gf_pow(a: int, e: int) -> int:
    r = 1
    while e:
        if e & 1:
            r = _gf_mul(r, a)
        a = _gf_mul(a, a)
        e >>= 1
    return r


def _build_sboxes():
    sbox = [0] * 256
    inv = [0] * 256
    for a in range(256):
        ia = 0 if a == 0 else _gf_pow(a, 254)
        y = 0
        for i in range(8):
            bit = (((ia >> i) & 1) ^ ((ia >> ((i + 4) % 8)) & 1)
                   ^ ((ia >> ((i + 5) % 8)) & 1) ^ ((ia >> ((i + 6) % 8)) & 1)
                   ^ ((ia >> ((i + 7) % 8)) & 1) ^ ((0x63 >> i) & 1))
            y |= bit << i
        sbox[a] = y
        inv[y] = a
    return sbox, inv

_SBOX, _INV_SBOX = _build_sboxes()
_RCON = [0] * 11
_acc = 1
for _i in range(1, 11):
    _RCON[_i] = _acc
    _acc = _gf_mul(_acc, 2)


def _rot_word(w: int) -> int:
    return ((w << 8) | (w >> 24)) & 0xFFFFFFFF


def _sub_word(w: int) -> int:
    return ((_SBOX[(w >> 24) & 0xFF] << 24) | (_SBOX[(w >> 16) & 0xFF] << 16)
            | (_SBOX[(w >> 8) & 0xFF] << 8) | _SBOX[w & 0xFF])


def _key_expansion(key: bytes) -> list:
    nk = len(key) // 4
    nr = nk + 6
    words = [int.from_bytes(key[4 * i:4 * i + 4], "big") for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = words[i - 1]
        if i % nk == 0:
            temp = (_sub_word(_rot_word(temp)) ^ (_RCON[i // nk] << 24)) & 0xFFFFFFFF
        elif nk > 6 and i % nk == 4:
            temp = _sub_word(temp)
        words.append((words[i - nk] ^ temp) & 0xFFFFFFFF)
    blob = b"".join(w.to_bytes(4, "big") for w in words)
    return [blob[16 * r:16 * r + 16] for r in range(nr + 1)]


def _add_round_key(state: list, rk: bytes) -> None:
    for i in range(16):
        state[i] ^= rk[i]


def _aes_encrypt_block(block: bytes, round_keys: list) -> bytes:
    s = list(block)
    _add_round_key(s, round_keys[0])
    for rnd in range(1, len(round_keys) - 1):
        s = [_SBOX[b] for b in s]
        for r in range(1, 4):
            row = [s[r + 4 * c] for c in range(4)]
            for c in range(4):
                s[r + 4 * c] = row[(c + r) % 4]
        for c in range(4):
            a0, a1, a2, a3 = s[c * 4], s[c * 4 + 1], s[c * 4 + 2], s[c * 4 + 3]
            s[c * 4] = _gf_mul(a0, 2) ^ _gf_mul(a1, 3) ^ a2 ^ a3
            s[c * 4 + 1] = a0 ^ _gf_mul(a1, 2) ^ _gf_mul(a2, 3) ^ a3
            s[c * 4 + 2] = a0 ^ a1 ^ _gf_mul(a2, 2) ^ _gf_mul(a3, 3)
            s[c * 4 + 3] = _gf_mul(a0, 3) ^ a1 ^ a2 ^ _gf_mul(a3, 2)
        _add_round_key(s, round_keys[rnd])
    s = [_SBOX[b] for b in s]
    for r in range(1, 4):
        row = [s[r + 4 * c] for c in range(4)]
        for c in range(4):
            s[r + 4 * c] = row[(c + r) % 4]
    _add_round_key(s, round_keys[-1])
    return bytes(s)


def _aes_decrypt_block(block: bytes, round_keys: list) -> bytes:
    s = list(block)
    _add_round_key(s, round_keys[-1])
    for rnd in range(len(round_keys) - 2, 0, -1):
        for r in range(1, 4):
            row = [s[r + 4 * c] for c in range(4)]
            for c in range(4):
                s[r + 4 * c] = row[(c - r) % 4]
        s = [_INV_SBOX[b] for b in s]
        _add_round_key(s, round_keys[rnd])
        for c in range(4):
            a0, a1, a2, a3 = s[c * 4], s[c * 4 + 1], s[c * 4 + 2], s[c * 4 + 3]
            s[c * 4] = _gf_mul(a0, 14) ^ _gf_mul(a1, 11) ^ _gf_mul(a2, 13) ^ _gf_mul(a3, 9)
            s[c * 4 + 1] = _gf_mul(a0, 9) ^ _gf_mul(a1, 14) ^ _gf_mul(a2, 11) ^ _gf_mul(a3, 13)
            s[c * 4 + 2] = _gf_mul(a0, 13) ^ _gf_mul(a1, 9) ^ _gf_mul(a2, 14) ^ _gf_mul(a3, 11)
            s[c * 4 + 3] = _gf_mul(a0, 11) ^ _gf_mul(a1, 13) ^ _gf_mul(a2, 9) ^ _gf_mul(a3, 14)
    for r in range(1, 4):
        row = [s[r + 4 * c] for c in range(4)]
        for c in range(4):
            s[r + 4 * c] = row[(c - r) % 4]
    s = [_INV_SBOX[b] for b in s]
    _add_round_key(s, round_keys[0])
    return bytes(s)


def _gf128_mul(x: int, y: int) -> int:
    z = 0
    v = x
    for i in range(128):
        if (y >> (127 - i)) & 1:
            z ^= v
        lsb = v & 1
        v >>= 1
        if lsb:
            v ^= 0xE1000000000000000000000000000000
    return z


def _ghash(h: bytes, aad: bytes, ct: bytes) -> bytes:
    h_int = int.from_bytes(h, "big")
    data = aad + b"\x00" * ((-len(aad)) % 16) + ct + b"\x00" * ((-len(ct)) % 16)
    data += (len(aad) * 8).to_bytes(8, "big") + (len(ct) * 8).to_bytes(8, "big")
    y = 0
    for i in range(0, len(data), 16):
        y = _gf128_mul(y ^ int.from_bytes(data[i:i + 16], "big"), h_int)
    return y.to_bytes(16, "big")


def _gctr(key: bytes, icb: int, data: bytes) -> bytes:
    round_keys = _key_expansion(key)
    out = bytearray()
    counter = icb
    for i in range(0, len(data), 16):
        keystream = _aes_encrypt_block(counter.to_bytes(16, "big"), round_keys)
        block = data[i:i + 16]
        out.extend(bytes(b ^ k for b, k in zip(block, keystream)))
        counter = ((counter >> 32) << 32) | (((counter & 0xFFFFFFFF) + 1) & 0xFFFFFFFF)
    return bytes(out)


def aes_gcm_encrypt(key: bytes, iv: bytes, plaintext: bytes, aad: bytes = b"") -> tuple:
    h = _aes_encrypt_block(b"\x00" * 16, _key_expansion(key))
    j0 = int.from_bytes(iv + b"\x00\x00\x00\x01", "big")
    inc = ((j0 >> 32) << 32) | (((j0 & 0xFFFFFFFF) + 1) & 0xFFFFFFFF)
    ct = _gctr(key, inc, plaintext)
    s = _ghash(h, aad, ct)
    tag = bytes(a ^ b for a, b in zip(
        _aes_encrypt_block(j0.to_bytes(16, "big"), _key_expansion(key)), s))
    return ct, tag


def aes_gcm_decrypt(key: bytes, iv: bytes, ct: bytes, tag: bytes, aad: bytes = b"") -> bytes:
    h = _aes_encrypt_block(b"\x00" * 16, _key_expansion(key))
    j0 = int.from_bytes(iv + b"\x00\x00\x00\x01", "big")
    s = _ghash(h, aad, ct)
    expect = bytes(a ^ b for a, b in zip(
        _aes_encrypt_block(j0.to_bytes(16, "big"), _key_expansion(key)), s))
    if not hmac.compare_digest(expect, tag):
        raise ValueError("AES-GCM tag mismatch")
    inc = ((j0 >> 32) << 32) | (((j0 & 0xFFFFFFFF) + 1) & 0xFFFFFFFF)
    return _gctr(key, inc, ct)


def aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    round_keys = _key_expansion(key)
    out = bytearray()
    prev = iv
    for i in range(0, len(data), 16):
        dec = _aes_decrypt_block(data[i:i + 16], round_keys)
        out.extend(bytes(b ^ p for b, p in zip(dec, prev)))
        prev = data[i:i + 16]
    return bytes(out)


def make_aa_req(key: bytes, epoch, build_id: str, query_hash: str, lane: str = CONTENT_LANE) -> str:
    ts = (int(time.time() * 1000) // AA_REQ_MS) * AA_REQ_MS
    payload = json.dumps({"v": 1, "ts": ts, "epoch": epoch, "buildId": build_id,
                          "qh": query_hash, "k": lane}, separators=(",", ":")).encode("utf-8")
    iv = hashlib.sha256(f"{epoch}:{build_id}:{query_hash}:{ts}:{lane}".encode("utf-8")).digest()[:12]
    ct, tag = aes_gcm_encrypt(bytes(key), iv, payload)
    return base64.b64encode(b"\x01" + iv + ct + tag).decode("ascii")


def decrypt_tobeparsed(b64: str, key: bytes):
    buf = base64.b64decode(b64)
    if not buf or buf[0] != 1:
        raise RuntimeError(f"Unsupported MKissa encryption version: {buf[0] if buf else None}")
    iv = buf[1:13]
    body = buf[13:]
    ct, tag = body[:-16], body[-16:]
    plain = aes_gcm_decrypt(bytes(key), bytes(iv), bytes(ct), bytes(tag))
    return json.loads(plain.decode("utf-8"))


def episode_query() -> str:
    zt = "\ntbObj {\n  u\n  sm\n  md\n  ts\n}\n"
    pu = "\n_id\nname\nenglishName\nnativeName\nslugTime\n"
    xa = (f"\n{pu}\nthumbnail\n{zt}\nlastEpisodeInfo\nlastEpisodeDate\ntype\nseason\n"
          "score\nairedStart\navailableEpisodes\nepisodeDuration\nepisodeCount\n"
          "lastUpdateEnd\ncharacterCount\n")
    ef = ("\n  _id\n  username\n  displayName\n  createdAt\n  picture\n  reputation\n"
          "  roleLevel\n\n  \n  brief\n  followerCount\n  followingCount\n  pDec\n"
          "  equippedBadgeKey\n  equippedBadge {\n    key\n    name\n    rank\n"
          "    iconPath\n    date\n  }\n  ugcContributorStats {\n"
          "    mediaEditReviewSubmitCount\n    mediaEditApprovedCount\n"
          "    mediaEditRejectedCount\n    mediaEditAppliedCount\n"
          "    mediaEditContributionPoints\n    mediaEditModContributionPoints\n  }\n"
          "\n  hideMe\n")
    fr = ("\nviews\nlikesCount\ncommentCount\ndislikesCount\nboostsCount\nreviewCount\n"
          "userScoreCount\nuserScoreTotalValue\nuserScoreAverValue\nviewers{\nfirstViewers{\n"
          f"viewCount\nlastWatchedDate\nuser{{\n{ef}\n}}\n}}\nrecViewers{{\nviewCount\n"
          f"lastWatchedDate\nuser{{\n{ef}\n}}\n}}\n}}\n")
    return ("\nquery(\n$showId: String!\n$translationType: VaildTranslationTypeEnumType!\n"
            "$episodeString: String!\n) {\nepisode(\nshowId: $showId\n"
            "translationType: $translationType\nepisodeString: $episodeString\n) {\n"
            "episodeString\nuploadDate\nsourceUrls\nthumbnail\nnotes\nshow{\n"
            f"{xa}\ndescription\nbroadcastInterval\nbanner\ncharacters\n"
            "availableEpisodesDetail\nnameOnlyString\ncharacters\nisAdult\nrelatedShows\n"
            "relatedMangas\naltNames\ndisqusIds\n}\npageStatus{\n_id\nnotes\npageId\n"
            f"showId\n{fr}\n}}\nepisodeInfo{{\nnotes\nthumbnails\n{zt}\nvidInforssub\n"
            "uploadDates\nvidInforsdub\nvidInforsraw\ndescription\n}\nversionFix\n}\n}\n")


async def api_post(query: str, variables: dict, build_id=None, extensions=None) -> dict:
    if build_id is None:
        config = await discover_crypto_config()
        build_id = config["buildId"]
    body = {"query": query, "variables": variables}
    if extensions is not None:
        body["extensions"] = extensions
    res = await _api_request("POST", API_URL,
                             api_headers(build_id, {"Content-Type": "application/json"}),
                             json_body=body)
    raw = res.text
    if res.status_code != 200:
        err = RuntimeError(f"API POST {res.status_code}")
        err.raw_body = raw
        raise err
    try:
        payload = json.loads(raw)
    except Exception:
        err = RuntimeError("API POST invalid JSON")
        err.raw_body = raw
        raise err
    errors = payload.get("errors") or []
    if errors:
        messages = [(e.get("message") or (e.get("extensions") or {}).get("code")
                     or "GraphQL error") for e in errors]
        err = (NeedCaptchaError(" \u00b7 ".join(messages)) if "NEED_CAPTCHA" in messages
               else RuntimeError(" \u00b7 ".join(messages)))
        err.raw_body = raw
        err.graphql = payload
        raise err
    return payload.get("data") or {}


async def api_episode(query: str, variables: dict, force: bool = False,
                      captcha_retry: int = 0, captcha=None, post_fallback: bool = False) -> dict:
    digest = sha256_hex(query)
    lane = await get_lane_key(CONTENT_LANE, force)
    key, epoch, build_id = lane["key"], lane["epoch"], lane["buildId"]
    extensions = {"persistedQuery": {"version": 1, "sha256Hash": digest},
                  "k": CONTENT_LANE,
                  "aaReq": make_aa_req(key, epoch, build_id, digest, CONTENT_LANE)}
    if captcha:
        extensions["captcha"] = captcha
    if captcha:
        posted = await api_post(query, variables, build_id, extensions)
        return decrypt_tobeparsed(posted["tobeparsed"], key) if posted.get("tobeparsed") else posted
    enc = lambda v: quote(v, safe="!~*'()-._")
    url = (f"{API_URL}?variables={enc(json.dumps(variables, separators=(',', ':')))}"
           f"&extensions={enc(json.dumps(extensions, separators=(',', ':')))}")
    res = await _api_request("GET", url, api_headers(build_id))
    raw = res.text
    if res.status_code != 200:
        err = RuntimeError(f"API {res.status_code}")
        err.raw_body = raw
        raise err
    try:
        payload = json.loads(raw)
    except Exception:
        err = RuntimeError("API invalid JSON")
        err.raw_body = raw
        raise err
    errors = payload.get("errors") or []
    messages = [(e.get("message") or (e.get("extensions") or {}).get("code") or "")
                for e in errors]
    messages = [m for m in messages if m]
    if any(m == "PersistedQueryNotFound" or re.search(r"Context creation failed", m, re.IGNORECASE)
           for m in messages):
        posted = await api_post(query, variables, build_id, extensions)
        return decrypt_tobeparsed(posted["tobeparsed"], key) if posted.get("tobeparsed") else posted
    if "NEED_CAPTCHA" in messages:
        if not post_fallback:
            try:
                post_hash = sha256_hex(query)
                post_ext = {"persistedQuery": {"version": 1, "sha256Hash": post_hash},
                            "k": CONTENT_LANE,
                            "aaReq": make_aa_req(key, epoch, build_id, post_hash, CONTENT_LANE)}
                posted = await api_post(query, variables, build_id, post_ext)
                return (decrypt_tobeparsed(posted["tobeparsed"], key)
                        if posted.get("tobeparsed") else posted)
            except NeedCaptchaError:
                pass
        if captcha_retry < CAPTCHA_RETRIES:
            await asyncio.sleep(1.5 + captcha_retry * 1.2)
            return await api_episode(query, variables, force=True,
                                     captcha_retry=captcha_retry + 1,
                                     post_fallback=True)
        err = NeedCaptchaError("MKissa requested captcha")
        err.raw_body = raw
        raise err
    if any(re.match(r"^AA_CRYPTO_", m) for m in messages):
        if not force:
            return await api_episode(query, variables, force=True,
                                     captcha_retry=captcha_retry, captcha=captcha,
                                     post_fallback=post_fallback)
        err = RuntimeError(" \u00b7 ".join(messages))
        err.raw_body = raw
        raise err
    data = payload.get("data") or {}
    if data.get("tobeparsed"):
        return decrypt_tobeparsed(data["tobeparsed"], key)
    if messages:
        err = RuntimeError(" \u00b7 ".join(messages))
        err.raw_body = raw
        raise err
    return data


async def search_mkissa(query: str, mode: str = "sub") -> list:
    gql = ("query($search:SearchInput $limit:Int $page:Int $translationType:"
           "VaildTranslationTypeEnumType $countryOrigin:VaildCountryOriginEnumType)"
           "{shows(search:$search limit:$limit page:$page translationType:$translationType "
           "countryOrigin:$countryOrigin){edges{_id name englishName nativeName slugTime "
           "availableEpisodes availableEpisodesDetail aniListId __typename}}}")
    data = await api_post(gql, {"search": {"allowAdult": False, "allowUnknown": False,
                                           "query": query},
                                "limit": 40, "page": 1, "translationType": mode,
                                "countryOrigin": "ALL"})
    return (data.get("shows") or {}).get("edges") or []


async def get_episode_sources(show_id: str, ep_num, audio: str = "sub", captcha=None):
    query = await discover_episode_query()
    data = await api_episode(query, {"showId": show_id, "translationType": audio,
                                     "episodeString": str(ep_num)}, captcha=captcha)
    return data.get("episode")


def slugify_title(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


async def warm_watch_page(show_id: str, show, ep_num, audio: str) -> None:
    show = show or {}
    slug = show.get("slugTime") or slugify_title(
        show.get("englishName") or show.get("name") or show.get("nativeName"))
    if not slug or not show_id:
        return
    page = f"{REFERER}/anime/{slug}-{show_id}/{audio}/{ep_num}"
    try:
        await _session_get_text(page, {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{REFERER}/", "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1", "Upgrade-Insecure-Requests": "1"})
    except Exception:
        pass


def _normalize(value) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _extract_year(title):
    if not title:
        return None
    m = re.search(r"\b(19[0-9]{2}|20[0-9]{2})\b", str(title))
    return int(m.group(1)) if m else None


def find_best_match(results: list, titles: list, target_year, target_id):
    normalized = [_normalize(t) for t in titles or []]
    normalized = [t for t in normalized if t]
    best = None
    best_score = float("-inf")
    for item in results or []:
        if target_id and item.get("aniListId") and str(item.get("aniListId")) == str(target_id):
            return item
        names = [_normalize(item.get(k)) for k in ("name", "englishName", "nativeName")]
        names = [n for n in names if n]
        if any(n in normalized for n in names):
            score = 100
        else:
            fuzzy = 0
            for name in names:
                for title in normalized:
                    if title in name or name in title:
                        value = min(len(name), len(title)) - abs(len(name) - len(title)) * 0.1
                        fuzzy = max(fuzzy, value)
            score = fuzzy
        item_year = (_extract_year(item.get("name")) or _extract_year(item.get("englishName"))
                     or _extract_year(item.get("nativeName")))
        year_score = 0
        if target_year and item_year:
            year_score = 50 if item_year == target_year else -200
        total = score + year_score
        if total > best_score:
            best_score = total
            best = item
    return best or (list(results or [])[:1] or [None])[0]


async def resolve_series(anilist_id: int, ctx=None) -> dict:
    ctx = ctx or await build_ctx(int(anilist_id))
    key = f"np:mkissa:{anilist_id}"
    hit = _cache.cached(key, _cache.SHOW_IDENTITY_TTL)
    if hit is not None:
        return hit
    media = ctx.get("media") or {}
    anizip = ctx.get("anizip") or {}
    titles = []
    for title in build_titles(media, anizip) + list((anizip.get("titles") or {}).values()):
        if title and isinstance(title, str) and title not in titles:
            titles.append(title)
    if not titles:
        ap_id = (anizip.get("mappings") or {}).get("animeplanet_id")
        if ap_id:
            titles = [" ".join(w[:1].upper() + w[1:] for w in re.split(r"[-_]", ap_id) if w)]
    if not titles:
        raise RuntimeError(f"Could not resolve titles for AniList ID: {anilist_id}")
    start = media.get("startDate") or {}
    target_year = media.get("seasonYear") or start.get("year")
    seen = set()
    results = []
    for title in titles[:3]:
        try:
            edges = await search_mkissa(title, "sub")
        except Exception:
            continue
        for edge in edges or []:
            eid = edge.get("_id")
            if eid and eid not in seen:
                seen.add(eid)
                results.append(edge)
    if not results:
        raise RuntimeError(f"No MKissa match for {titles[0]!r}")
    match = find_best_match(results, titles, target_year, anilist_id)
    if not match:
        raise RuntimeError(f"No MKissa match for {titles[0]!r}")
    exact = bool(match.get("aniListId")) and str(match.get("aniListId")) == str(anilist_id)
    data = {"show_id": match.get("_id"),
            "title": match.get("englishName") or match.get("name"),
            "show": match, "mode": "local", "offset": 0,
            "score": 1.0 if exact else 0.85}
    _cache.set(key, data, _cache.SHOW_IDENTITY_TTL)
    return data


async def _fresh_show(series: dict) -> dict:
    try:
        edges = await search_mkissa(series.get("title") or "", "sub")
    except Exception:
        return series.get("show") or {}
    for edge in edges or []:
        if edge.get("_id") == series.get("show_id"):
            return edge
    return series.get("show") or {}


def _coerce_numbers(values) -> list:
    nums = set()
    for value in values or []:
        if isinstance(value, bool):
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            try:
                n = int(float(str(value)))
            except (TypeError, ValueError):
                continue
        if n > 0:
            nums.add(n)
    return sorted(nums)


def _build_lists(anilist_id: int, series: dict, sub_nums: list, dub_nums: list, ctx: dict, expected) -> dict:
    out = {"sub": [], "dub": [], "raw": []}
    for audio, nums in (("sub", sub_nums), ("dub", dub_nums)):
        for n in nums:
            if n < 1:
                continue
            if expected and n > expected:
                continue
            meta = episode_meta(n, ctx)
            out[audio].append({
                "id": f"watch/mkissa/{anilist_id}/{audio}/mkissa-{n}",
                "number": n,
                "title": meta["title"] or f"Episode {n}",
                "duration": meta["duration"],
                "audio": audio,
                "filler": meta["filler"],
                "uncensored": meta["uncensored"],
                "description": meta["description"],
                "image": meta["image"],
                "airDate": meta["airDate"],
                "sourceNumber": n,
            })
    return out


async def get_episodes(anilist_id: int, ctx=None) -> dict:
    ctx = ctx or await build_ctx(int(anilist_id))
    media = ctx.get("media") or {}
    series = await resolve_series(int(anilist_id), ctx)
    show = await _fresh_show(series)
    expected = expected_count(media, ctx.get("anizip"))
    detail = (show or {}).get("availableEpisodesDetail") or {}
    episodes = _build_lists(int(anilist_id), series, _coerce_numbers(detail.get("sub")), _coerce_numbers(detail.get("dub")), ctx, expected)
    return {
        "meta": {
            "id": series.get("show_id"),
            "title": series.get("title"),
            "source": NAME,
            "matchScore": round(float(series.get("score") or 0), 3),
            "numbering": series.get("mode") or "local",
            "episodeOffset": series.get("offset") or 0,
        },
        "episodes": episodes,
    }


def hex_to_bytes(hex_value: str) -> bytes:
    clean = re.sub(r"[^0-9a-fA-F]", "", hex_value or "")
    return bytes(int(clean[i:i + 2], 16) for i in range(0, len(clean), 2))


async def aes_decrypt(hex_value: str) -> str:
    plain = aes_cbc_decrypt(b"kiemtienmua911ca", b"1234567890oiuytr", hex_to_bytes(hex_value))
    return plain.decode("utf-8")


async def extract_mp4(embed_id: str):
    try:
        res = await _raw_get(f"https://www.mp4upload.com/embed-{embed_id}.html", {"User-Agent": UA4, "Referer": "https://mp4upload.com/"})
        if res.status_code != 200:
            return None
        html = res.text
        m = re.search(r'''player\.src\s*\(\s*\{[^}]*\bsrc\s*:\s*"([^"]+)"''', html)
        if not m:
            m = re.search(r'''"file"\s*:\s*"(https?:[^"]+\.mp4[^"]*)"''', html)
        if not m:
            m = re.search(r'''\bsrc\s*:\s*"(https?:[^"]+\.mp4[^"]*)"''', html)
        return m.group(1).replace("\\", "") if m else None
    except Exception:
        return None


async def extract_uns(url: str):
    try:
        parsed = urlparse(url)
        vid = (parsed.fragment or "").split("&")[0]
        if not vid:
            return None
        base = f"{parsed.scheme}//{parsed.netloc}"
        res = await _raw_get(base + "/api/v1/video?id=" + quote(vid, safe='') + "&w=1280&h=720&r=", {"User-Agent": UA4, "Referer": base + "/#" + vid, "Origin": base})
        if res.status_code != 200:
            return None
        hex_value = res.text.strip()
        if not hex_value or not re.fullmatch(r"[0-9a-fA-F]+", hex_value):
            return None
        data = json.loads(await aes_decrypt(hex_value))
        return data.get("source") or data.get("cf")
    except Exception:
        return None


async def extract_ok(embed_id: str):
    try:
        res = await _raw_get(f"https://ok.ru/videoembed/{embed_id}", {"User-Agent": UA4, "Referer": "https://ok.ru/"})
        if res.status_code != 200:
            return None
        m = re.search(r'''ondemandHls\\&quot;:\\&quot;(https?://.*?)\\&quot;''', res.text)
        return m.group(1).replace("\\u0026", "&") if m else None
    except Exception:
        return None


async def extract_stream_sb(embed_id: str):
    try:
        base_headers = {"User-Agent": UA4, "Referer": f"{REFERER}/", "watchsb": "streamsb", "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
        r1 = await _raw_get(f"https://streamsb.net/api/v1/video?id={embed_id}", base_headers)
        sid_m = re.search(r"sid=([^;]+)", r1.headers.get("set-cookie", ""))
        sid = sid_m.group(1) if sid_m else ""
        m = re.search(r'''window\.location\.replace\('([^']+)'\)''', r1.text or "")
        if not m:
            return None
        data = await fetch_json(m.group(1), dict(base_headers, **{"Cookie": f"sid={sid}", "Referer": f"https://streamsb.net/e/{embed_id}.html"}))
        return ((data.get("stream_data") or {}).get("file") or (data.get("data") or {}).get("file"))
    except Exception:
        return None


async def extract_clock(url: str):
    try:
        parsed = urlparse(url)
        clock_url = url if "/clock.json" in (parsed.path or "") else url.replace("/clock", "/clock.json", 1)
        data = await fetch_json(clock_url, {"User-Agent": UA4, "Referer": "https://allanime.day/player.html", "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"})
        links = data.get("links") if isinstance(data, dict) else None
        links = links if isinstance(links, list) else []
        best = next((item for item in links if isinstance(item, dict) and item.get("hls") and item.get("link")), None)
        best = best or next((item for item in links if isinstance(item, dict) and item.get("link")), None)
        return best.get("link") if best else None
    except Exception:
        return None


async def extract_streamlare(embed_id: str):
    try:
        res = await _raw_post("https://streamlare.com/api/video/stream/get", {"id": embed_id}, {"Content-Type": "application/json", "User-Agent": UA4, "Referer": "https://streamlare.com/", "Origin": "https://streamlare.com", "Accept": "application/json, */*"})
        if res.status_code != 200:
            return None
        data = res.json()
        return (data.get("data") or {}).get("file")
    except Exception:
        return None


def embed_media_type(url):
    if not url:
        return None
    if ".m3u8" in url:
        return "hls"
    if ".mp4" in url:
        return "mp4"
    return "direct"


def is_clock_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower()
        path = parsed.path or ""
        return host == "allanime.day" and re.search("/apivtwo/clock", path) is not None
    except Exception:
        return False


async def extract_source(src: dict) -> dict:
    src = src or {}
    url = src.get("sourceUrl")
    if url and url.startswith("--"):
        url = decode_hex_url(url[2:])
    if url and url.startswith("/apivtwo/clock"):
        url = "https://allanime.day" + url.replace("/clock", "/clock.json", 1)
    if url and re.match("https?://allanime[.]day/apivtwo/clock", url or "", re.IGNORECASE):
        if "/clock?" in url:
            url = url.replace("/clock?", "/clock.json?", 1)
    extracted_url = None
    try:
        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower().removeprefix("www.")
        path = parsed.path or ""
        if host == "allanime.day" and re.search("/apivtwo/clock", path, re.IGNORECASE):
            extracted_url = await extract_clock(url)
        elif src.get("type") == "player":
            extracted_url = url
        elif host == "mp4upload.com":
            m = re.search(r"embed-([a-zA-Z0-9]+)[.]html", url or "", re.IGNORECASE)
            if m:
                extracted_url = await extract_mp4(m.group(1))
        elif host == "uns.bio" or host.endswith(".uns.bio"):
            extracted_url = await extract_uns(url)
        elif host == "ok.ru":
            m = re.search("/(?:videoembed/)?([0-9]+)(?:[/?#]|$)", url or "", re.IGNORECASE)
            if m:
                extracted_url = await extract_ok(m.group(1))
        elif "streamsb." in host:
            m = re.search("/(?:e/|embed-)([a-zA-Z0-9]+)", url or "", re.IGNORECASE)
            if m:
                extracted_url = await extract_stream_sb(m.group(1))
        elif "streamlare." in host:
            m = re.search("/e/([a-zA-Z0-9]+)", url or "", re.IGNORECASE)
            if m:
                extracted_url = await extract_streamlare(m.group(1))
    except Exception:
        pass
    if extracted_url:
        extracted_url = decode_entities(extracted_url)
    return {"name": decode_entities(src.get("sourceName") or ""), "url": url, "extractedUrl": extracted_url, "extractedType": embed_media_type(extracted_url), "type": src.get("type"), "priority": src.get("priority"), "headers": {"Referer": REFERER, "User-Agent": UA4}, "downloads": src.get("downloads")}


def _map_source(item: dict, audio: str):
    url = item.get("extractedUrl") or item.get("url")
    if not url:
        return None
    if is_clock_url(item.get("url")) and not item.get("extractedUrl"):
        return None
    headers = item.get("headers") or {}
    return {"url": url, "type": item.get("extractedType") or "hls", "server": item.get("name") or "MKissa", "audio": audio, "referer": headers.get("Referer") or REFERER}


async def watch(anilist_id: int, audio: str, ep: int, ctx=None) -> list:
    if audio not in ("sub", "dub", "all"):
        raise ValueError(f"audio must be one of sub/dub/all, got {audio!r}")
    ctx = ctx or await build_ctx(int(anilist_id))
    ep_num = int(ep)
    cache_key = f"{anilist_id}:{audio}:{ep_num}"
    captcha = ctx.get("captcha") if isinstance(ctx.get("captcha"), dict) else None
    entry = _watch_cache.get(cache_key)
    if entry and entry.get("expires_at", 0) > time.time() and not captcha:
        return entry["data"]
    series = await resolve_series(int(anilist_id), ctx)
    show_id = series.get("show_id")
    if not show_id:
        raise RuntimeError(f"No MKissa match for AniList {anilist_id}")
    if not captcha:
        await warm_watch_page(show_id, series.get("show"), ep_num, "sub" if audio == "all" else audio)
    audios = ["sub", "dub"] if audio == "all" else [audio]
    try:
        episodes = await asyncio.gather(*[get_episode_sources(show_id, ep_num, aud, captcha) for aud in audios])
    except NeedCaptchaError:
        if entry and entry.get("data"):
            return entry["data"]
        raise
    ranked = []
    for aud, episode in zip(audios, episodes):
        for src in (episode or {}).get("sourceUrls") or []:
            try:
                item = await extract_source(src)
            except Exception:
                continue
            mapped = _map_source(item, aud)
            if mapped:
                ranked.append((item.get("priority") or 0, mapped))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    streams = [mapped for _, mapped in ranked]
    _watch_cache[cache_key] = {"data": streams, "expires_at": time.time() + WATCH_MEMORY_TTL}
    return streams
