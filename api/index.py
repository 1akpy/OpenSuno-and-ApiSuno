from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import re

app = FastAPI(title="SunoAPI", description="Unofficial Suno track resolver API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://suno.com/",
}

def ok(data):
    return JSONResponse({"status": "ok", "data": data})

def err(msg: str, code: int = 400):
    return JSONResponse({"status": "error", "message": msg, "data": None}, status_code=code)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SONG_RE = re.compile(r"suno\.com/song/([0-9a-f-]+)", re.I)

def extract_id_from_url(url: str) -> str | None:
    m = SONG_RE.search(url)
    if m: return m.group(1)
    m = UUID_RE.search(url)
    if m: return m.group(0)
    return None

async def resolve_short_url(short_url: str) -> str:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=HEADERS) as client:
        r = await client.get(short_url)
        return str(r.url)

async def get_track_id(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    if "/s/" in raw:
        resolved = await resolve_short_url(raw)
        track_id = extract_id_from_url(resolved)
        if not track_id:
            raise ValueError(f"Could not extract ID from: {resolved}")
        return track_id
    track_id = extract_id_from_url(raw)
    if not track_id:
        raise ValueError("No track ID found in URL")
    return track_id

async def fetch_metadata(track_id: str) -> dict | None:
    urls = [
        f"https://studio-api.suno.ai/api/clip/{track_id}/",
        f"https://studio-api.prod.suno.ai/api/clip/{track_id}/",
    ]
    async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
        for url in urls:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                continue
    return None

def _get_artist(clip: dict) -> str | None:
    for key in ("display_name", "user_display_name", "handle", "author", "username"):
        v = clip.get(key)
        if v: return v
    profiles = clip.get("profiles") or []
    if profiles and isinstance(profiles, list):
        return profiles[0].get("display_name") or profiles[0].get("handle")
    return None

def _get_genre(clip: dict) -> str | None:
    meta = clip.get("metadata") or {}
    return meta.get("genre") or meta.get("style") or clip.get("style")

def _get_prompt(clip: dict) -> str | None:
    meta = clip.get("metadata") or {}
    return meta.get("prompt") or meta.get("gpt_description_prompt") or clip.get("prompt")

def build_track_data(track_id: str, meta: dict | None) -> dict:
    cdn = {
        "mp3":       f"https://cdn1.suno.ai/{track_id}.mp3",
        "cover_jpg": f"https://cdn2.suno.ai/image_{track_id}.jpeg",
        "cover_png": f"https://cdn2.suno.ai/image_{track_id}.png",
    }

    base = {
        "id":        track_id,
        "suno_url":  f"https://suno.com/song/{track_id}",
        "mp3_url":   cdn["mp3"],
        "cover_url": cdn["cover_jpg"],
        "cover_png": cdn["cover_png"],
        "download":  cdn,
        # metadata fields — null if unavailable
        "title":        None,
        "artist":       None,
        "tags":         None,
        "genre":        None,
        "duration":     None,
        "created_at":   None,
        "is_public":    None,
        "play_count":   None,
        "upvote_count": None,
        "model":        None,
        "prompt":       None,
    }

    if not meta:
        return base

    clip = meta.get("clip") or meta

    base.update({
        "title":        clip.get("title")          or clip.get("display_name"),
        "artist":       _get_artist(clip),
        "tags":         clip.get("tags")           or (clip.get("metadata") or {}).get("tags"),
        "genre":        _get_genre(clip),
        "duration":     clip.get("duration")       or clip.get("audio_duration"),
        "created_at":   clip.get("created_at"),
        "is_public":    clip.get("is_public"),
        "play_count":   clip.get("play_count"),
        "upvote_count": clip.get("upvote_count"),
        "model":        clip.get("model_version")  or clip.get("model"),
        "prompt":       _get_prompt(clip),
        "mp3_url":      clip.get("audio_url")      or cdn["mp3"],
        "cover_url":    clip.get("image_url")      or cdn["cover_jpg"],
        "cover_png":    clip.get("image_large_url") or cdn["cover_png"],
        "download": {
            "mp3":       clip.get("audio_url")       or cdn["mp3"],
            "cover_jpg": clip.get("image_url")       or cdn["cover_jpg"],
            "cover_png": clip.get("image_large_url") or cdn["cover_png"],
        }
    })

    return base

@app.get("/")
async def root():
    return {
        "status": "ok",
        "name": "SunoAPI",
        "endpoints": {
            "/track?url=<suno_link>": "Resolve any Suno URL",
            "/track/<uuid>":          "Lookup by track ID",
            "/docs":                  "Swagger UI",
        }
    }

@app.get("/track")
async def track(url: str = Query(..., description="Any Suno URL — short or full")):
    try:
        track_id = await get_track_id(url)
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:
        return err(f"Failed to resolve URL: {e}", 500)
    meta = await fetch_metadata(track_id)
    return ok(build_track_data(track_id, meta))

@app.get("/track/{track_id}")
async def track_by_id(track_id: str):
    if not UUID_RE.fullmatch(track_id):
        return err("Invalid track ID — must be a UUID", 400)
    meta = await fetch_metadata(track_id)
    return ok(build_track_data(track_id, meta))
