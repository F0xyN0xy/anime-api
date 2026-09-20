BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
}

HEADERS = BASE_HEADERS

ANILIST_URL = "https://graphql.anilist.co"

MIRURO_BASE_URLS = [
    "https://www.miruro.ru",
    "https://www.miruro.bz",
]

MIRURO_PIPE_PATH = "/api/secure/pipe"


def iter_miruro_pipe_targets(encoded_req: str):

    for base in MIRURO_BASE_URLS:
        pipe_url = f"{base}{MIRURO_PIPE_PATH}?e={encoded_req}"
        headers = {
            **BASE_HEADERS,
            "Origin": base,
            "Referer": f"{base}/",
        }
        yield pipe_url, headers
