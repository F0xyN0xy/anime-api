from fastapi import APIRouter, Query, HTTPException
from typing import Optional
import httpx
import httpx

from src.queries import MEDIA_LIST_FIELDS, MEDIA_FULL_FIELDS
from src.parser import proxy_deep_images
from src.extractor import anilist_query
from src.providers import _race as provider_race

router = APIRouter()


@router.get("/search")
async def search_anime(
    query: str,
    page: int = Query(1, ge=1, description="page number"),
    per_page: int = Query(20, ge=1, le=50, description="results per page"),
):

    gql = f"""
    query ($search: String, $page: Int, $perPage: Int) {{
        Page(page: $page, perPage: $perPage) {{
            pageInfo {{ total currentPage lastPage hasNextPage perPage }}
            media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{
                {MEDIA_LIST_FIELDS}
            }}
        }}
    }}
    """
    data = await anilist_query(gql, {"search": query, "page": page, "perPage": per_page})
    page_data = data.get("Page", {})
    page_info = page_data.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": page_data.get("media", []),
    }
    return proxy_deep_images(response)


@router.get("/suggestions")
async def search_suggestions(
    query: str = Query(..., min_length=1, description="small search query for dropdowns"),
):

    gql = """
    query ($search: String) {
        Page(page: 1, perPage: 8) {
            media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
                id
                title { romaji english }
                coverImage { large }
                format
                status
                startDate { year }
                episodes
            }
        }
    }
    """
    data = await anilist_query(gql, {"search": query})
    results = []

    for item in data.get("Page", {}).get("media", []):
        results.append({
            "id": item["id"],
            "title": item["title"].get("english") or item["title"].get("romaji"),
            "title_romaji": item["title"].get("romaji"),
            "poster": item["coverImage"]["large"],
            "format": item.get("format"),
            "status": item.get("status"),
            "year": (item.get("startDate") or {}).get("year"),
            "episodes": item.get("episodes"),
        })
    return proxy_deep_images({"suggestions": results})

SORT_MAP = {
    "SCORE_DESC": "SCORE_DESC",
    "POPULARITY_DESC": "POPULARITY_DESC",
    "TRENDING_DESC": "TRENDING_DESC",
    "START_DATE_DESC": "START_DATE_DESC",
    "FAVOURITES_DESC": "FAVOURITES_DESC",
    "UPDATED_AT_DESC": "UPDATED_AT_DESC",
}


@router.get("/filter")
async def filter_anime(
    genre: Optional[str] = Query(None, description="genre name"),
    tag: Optional[str] = Query(None, description="tag name"),
    year: Optional[int] = Query(None, description="season year"),
    season: Optional[str] = Query(None, description="season time"),
    format: Optional[str] = Query(None, description="media format"),
    status: Optional[str] = Query(None, description="release status"),
    sort: str = Query("POPULARITY_DESC", description="ordering logic"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=50),
):

    args = ["type: ANIME", f"sort: [{SORT_MAP.get(sort, 'POPULARITY_DESC')}]"]
    variables = {"page": page, "perPage": per_page}

    if genre:
        args.append("genre: $genre")
        variables["genre"] = genre
    if tag:
        args.append("tag: $tag")
        variables["tag"] = tag
    if year:
        args.append("seasonYear: $seasonYear")
        variables["seasonYear"] = year
    if season:
        args.append("season: $season")
        variables["season"] = season.upper()
    if format:
        args.append("format: $format")
        variables["format"] = format.upper()
    if status:
        args.append("status: $status")
        variables["status"] = status.upper()

    var_types = ["$page: Int", "$perPage: Int"]
    if genre:
        var_types.append("$genre: String")
    if tag:
        var_types.append("$tag: String")
    if year:
        var_types.append("$seasonYear: Int")
    if season:
        var_types.append("$season: MediaSeason")
    if format:
        var_types.append("$format: MediaFormat")
    if status:
        var_types.append("$status: MediaStatus")

    gql = f"""
    query ({', '.join(var_types)}) {{
        Page(page: $page, perPage: $perPage) {{
            pageInfo {{ total currentPage lastPage hasNextPage perPage }}
            media({', '.join(args)}) {{
                {MEDIA_LIST_FIELDS}
            }}
        }}
    }}
    """

    data = await anilist_query(gql, variables)
    page_data = data.get("Page", {})
    page_info = page_data.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": page_data.get("media", []),
    }
    return proxy_deep_images(response)


async def _fetch_collection(sort_type: str, status: str = None, page: int = 1, per_page: int = 20):
    status_filter = f", status: {status}" if status else ""
    gql = f"""
    query ($page: Int, $perPage: Int) {{
        Page(page: $page, perPage: $perPage) {{
            pageInfo {{ total currentPage lastPage hasNextPage perPage }}
            media(type: ANIME, sort: [{sort_type}]{status_filter}) {{
                {MEDIA_LIST_FIELDS}
            }}
        }}
    }}
    """
    data = await anilist_query(gql, {"page": page, "perPage": per_page})
    page_data = data.get("Page", {})
    page_info = page_data.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": page_data.get("media", []),
    }
    return proxy_deep_images(response)


@router.get("/spotlight")
async def get_spotlight():

    gql = f"""
    query {{
        Page(page: 1, perPage: 10) {{
            media(sort: [TRENDING_DESC, POPULARITY_DESC], type: ANIME) {{
                {MEDIA_LIST_FIELDS}
            }}
        }}
    }}
    """
    data = await anilist_query(gql)
    media = data.get("Page", {}).get("media", [])
    return proxy_deep_images({"results": media})


@router.get("/trending")
async def get_trending(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    return await _fetch_collection("TRENDING_DESC", page=page, per_page=per_page)


@router.get("/popular")
async def get_popular(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    return await _fetch_collection("POPULARITY_DESC", page=page, per_page=per_page)


@router.get("/upcoming")
async def get_upcoming(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    return await _fetch_collection("POPULARITY_DESC", "NOT_YET_RELEASED", page=page, per_page=per_page)


@router.get("/recent")
async def get_recent(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    return await _fetch_collection("START_DATE_DESC", "RELEASING", page=page, per_page=per_page)


@router.get("/schedule")
async def get_schedule(page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=50)):

    gql = f"""
    query ($page: Int, $perPage: Int) {{
        Page(page: $page, perPage: $perPage) {{
            pageInfo {{ total currentPage lastPage hasNextPage perPage }}
            airingSchedules(notYetAired: true, sort: TIME) {{
                episode
                airingAt
                timeUntilAiring
                media {{
                    {MEDIA_LIST_FIELDS}
                }}
            }}
        }}
    }}
    """
    data = await anilist_query(gql, {"page": page, "perPage": per_page})
    page_data = data.get("Page", {})
    page_info = page_data.get("pageInfo", {})

    results = []
    for item in page_data.get("airingSchedules", []):
        entry = item.get("media", {})
        entry["next_episode"] = item.get("episode")
        entry["airingAt"] = item.get("airingAt")
        entry["timeUntilAiring"] = item.get("timeUntilAiring")
        results.append(entry)

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": results,
    }
    return proxy_deep_images(response)


@router.get("/info/{anilist_id}")
async def get_anime_info(anilist_id: int):

    gql = f"""
    query ($id: Int) {{
        Media(id: $id, type: ANIME) {{
            {MEDIA_FULL_FIELDS}
        }}
    }}
    """
    data = await anilist_query(gql, {"id": anilist_id})
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="anime not found sorry")
    return proxy_deep_images(media)


@router.get("/anime/{anilist_id}/characters")
async def get_anime_characters(
    anilist_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=50),
):

    gql = """
    query ($id: Int, $page: Int, $perPage: Int) {
        Media(id: $id, type: ANIME) {
            id
            title { romaji english }
            characters(sort: [ROLE, RELEVANCE], page: $page, perPage: $perPage) {
                pageInfo { total currentPage lastPage hasNextPage perPage }
                edges {
                    role
                    node {
                        id
                        name { full native userPreferred }
                        image { large medium }
                        description
                        gender
                        dateOfBirth { year month day }
                        age
                        favourites
                        siteUrl
                    }
                    voiceActors {
                        id
                        name { full native }
                        image { large }
                        languageV2
                    }
                }
            }
        }
    }
    """
    data = await anilist_query(gql, {"id": anilist_id, "page": page, "perPage": per_page})
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="couldnt locate anime")

    chars = media.get("characters", {})
    page_info = chars.get("pageInfo", {})

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "characters": chars.get("edges", []),
    }
    return proxy_deep_images(response)


@router.get("/anime/{anilist_id}/relations")
async def get_anime_relations(anilist_id: int):

    gql = """
    query ($id: Int) {
        Media(id: $id, type: ANIME) {
            id
            title { romaji english }
            relations {
                edges {
                    relationType(version: 2)
                    node {
                        id
                        title { romaji english native }
                        coverImage { large }
                        bannerImage
                        format
                        type
                        status
                        episodes
                        chapters
                        meanScore
                        averageScore
                        popularity
                        startDate { year month day }
                    }
                }
            }
        }
    }
    """
    data = await anilist_query(gql, {"id": anilist_id})
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="not found")

    response = {
        "id": media["id"],
        "title": media["title"],
        "relations": media.get("relations", {}).get("edges", []),
    }
    return proxy_deep_images(response)


@router.get("/anime/{anilist_id}/recommendations")
async def get_anime_recommendations(
    anilist_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=25),
):

    gql = """
    query ($id: Int, $page: Int, $perPage: Int) {
        Media(id: $id, type: ANIME) {
            id
            title { romaji english }
            recommendations(sort: RATING_DESC, page: $page, perPage: $perPage) {
                pageInfo { total currentPage lastPage hasNextPage perPage }
                nodes {
                    rating
                    mediaRecommendation {
                        id
                        title { romaji english native }
                        coverImage { large extraLarge }
                        bannerImage
                        format
                        episodes
                        status
                        meanScore
                        averageScore
                        popularity
                        genres
                        startDate { year }
                    }
                }
            }
        }
    }
    """
    data = await anilist_query(gql, {"id": anilist_id, "page": page, "perPage": per_page})
    media = data.get("Media")
    if not media:
        raise HTTPException(status_code=404, detail="where the anime at")

    recs = media.get("recommendations", {})
    page_info = recs.get("pageInfo", {})

    nodes = recs.get("nodes", [])
    results = [
        node.get("mediaRecommendation")
        for node in nodes
        if node and node.get("mediaRecommendation")
    ]

    response = {
        "page": page_info.get("currentPage", page),
        "perPage": page_info.get("perPage", per_page),
        "total": page_info.get("total", 0),
        "hasNextPage": page_info.get("hasNextPage", False),
        "results": results,
        "recommendations": nodes,
    }
    return proxy_deep_images(response)


@router.get("/episodes/{anilist_id}")
async def get_episodes(anilist_id: int):
    try:
        native = await provider_race.merged_episodes(anilist_id)
        if native:
            return proxy_deep_images(native)
    except Exception as e:
        print(f"[KUHI API] native episode discovery failed: {e}")
    raise HTTPException(status_code=503, detail="episode discovery providers are unavailable")


@router.get("/extract/{query}")
async def extract_simple(
    query: str,
    episode: int = Query(1, alias="e"),
    audio: str = Query("sub", alias="type", description="sub or dub"),
    provider: str | None = Query(None, alias="provider", description="prefer one provider"),
):

    audio = (audio or "sub").lower()
    if audio not in ("sub", "dub"):
        raise HTTPException(status_code=422, detail="type must be sub or dub")
    wanted = (provider or "").lower() or None
    if wanted and wanted not in provider_race.RANKING:
        raise HTTPException(status_code=422, detail=f"unknown provider '{provider}'. valid: {', '.join(provider_race.RANKING)}")

    if query.isdigit():
        anilist_id = int(query)

        gql = """
        query ($id: Int) {
            Media(id: $id, type: ANIME) {
                id
                format
            }
        }
        """
        media_data = await anilist_query(gql, {"id": anilist_id})
        media = media_data.get("Media")
        if not media:
            raise HTTPException(status_code=404, detail="anime not found")

        if media.get("format") == "MOVIE":
            episode = 1
    else:

        gql = f"""
        query ($search: String) {{
            Page(page: 1, perPage: 1) {{
                media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{
                    id
                    format
                }}
            }}
        }}
        """
        search_data = await anilist_query(gql, {"search": query.replace("-", " ")})
        media_list = search_data.get("Page", {}).get("media", [])
        if not media_list:
            raise HTTPException(status_code=404, detail="anime not found")

        anilist_id = media_list[0]["id"]
        media_format = media_list[0].get("format")

        if media_format == "MOVIE":
            episode = 1

    try:
        raced = await provider_race.race_watch(anilist_id, episode, audio, preferred=wanted)
    except Exception as e:
        print(f"[KUHI API] native provider race failed: {e}")
        raise HTTPException(status_code=503, detail="stream providers are temporarily unavailable")

    if not raced:
        raise HTTPException(status_code=404, detail=f"no {audio} streams found for episode {episode}")

    result = {
        "anilistId": anilist_id,
        "episode": episode,
        "type": audio,
        "provider": raced["provider"],
        "defaultProvider": raced.get("defaultProvider"),
        "requestedProvider": raced.get("requestedProvider"),
        "streams": raced["streams"],
        "subtitles": provider_race.merge_subtitles(raced["streams"]),
    }
    return proxy_deep_images(result)
