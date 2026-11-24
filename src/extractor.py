import asyncio

import httpx
from fastapi import HTTPException
from src.config import ANILIST_URL


async def anilist_query(query: str, variables: dict = None): # type: ignore

    body = {"query": query}
    if variables:
        body["variables"] = variables # type: ignore

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
        "Origin": "https://anilist.co",
        "Referer": "https://anilist.co/",
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

