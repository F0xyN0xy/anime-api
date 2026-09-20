import asyncio

import httpx
from fastapi import HTTPException
from src.config import ANILIST_URL, iter_miruro_pipe_targets
from src.parser import encode_pipe_request, decode_pipe_response, deep_translate


async def anilist_query(query: str, variables: dict = None):

    body = {"query": query}
    if variables:
        body["variables"] = variables

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Origin": "https://www.miruro.ru",
        "Referer": "https://www.miruro.ru/",
    }

    last_err = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(ANILIST_URL, json=body, headers=headers)
                if res.status_code == 429:
                    wait = 2 * (attempt + 1)
                    print(f"[KUHI API] anilist rate-limited, retrying in {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if res.status_code != 200:
                    raise HTTPException(status_code=500, detail="anilist query failed")
                return res.json().get("data", {})
        except HTTPException:
            raise
        except Exception as e:
            last_err = e
            print(f"[KUHI API] anilist query attempt {attempt + 1} failed: {type(e).__name__}: {e}")
            await asyncio.sleep(1.5 * (attempt + 1))
    raise HTTPException(status_code=502, detail=f"anilist unreachable: {last_err}")


async def fetch_raw_episodes(anilist_id: int) -> dict:
    payload = {
        "path": "episodes",
        "method": "GET",
        "query": {"anilistId": anilist_id},
        "body": None,
        "version": "0.1.0",
    }
    encoded_req = encode_pipe_request(payload)

    async with httpx.AsyncClient(timeout=15.0) as client:
        last_status = None
        for pipe_target, headers in iter_miruro_pipe_targets(encoded_req):
            print(f"\n[KUHI API] executing pipe for raw episodes:", pipe_target)
            try:
                res = await client.get(pipe_target, headers=headers)
                last_status = res.status_code
                if res.status_code == 200:
                    data = decode_pipe_response(res.text.strip())
                    deep_translate(data)
                    return data
            except Exception as e:
                print(f"[KUHI API] Failed to connect to {pipe_target}: {e}")

        raise HTTPException(status_code=last_status or 502, detail="pipe request failed")
