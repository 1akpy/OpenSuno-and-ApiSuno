from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import re

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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

def clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SONG_RE = re.compile(r"suno\.com/song/([0-9a-f-]+)", re.I)

def extract_id(url: str) -> str | None:
    m = SONG_RE.search(url)
    if m: return m.group(1)
    m = UUID_RE.search(url)
    if m: return m.group(0)
    return None

async def resolve_short(url: str) -> str:
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=HEADERS) as c:
        r = await c.get(url)
        return str(r.url)

async def get_id(raw: str) -> str:
    raw = raw.strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    if "/s/" in raw:
        resolved = await resolve_short(raw)
        tid = extract_id(resolved)
        if not tid: raise ValueError(f"Could not extract ID from: {resolved}")
        return tid
    tid = extract_id(raw)
    if not tid: raise ValueError("No track ID found in URL")
    return tid

async def fetch_meta(track_id: str) -> dict | None:
    for url in [
        f"https://studio-api.suno.ai/api/clip/{track_id}/",
        f"https://studio-api.prod.suno.ai/api/clip/{track_id}/",
    ]:
        try:
            async with httpx.AsyncClient(timeout=10, headers=HEADERS) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    return r.json()
        except Exception:
            continue
    return None

def build(track_id: str, meta: dict | None) -> dict:
    cdn_mp3 = f"https://cdn1.suno.ai/{track_id}.mp3"
    cdn_jpg = f"https://cdn2.suno.ai/image_{track_id}.jpeg"
    cdn_png = f"https://cdn2.suno.ai/image_{track_id}.png"
    clip    = (meta or {}).get("clip") or meta or {}
    m       = clip.get("metadata") or {}

    def g(*keys):
        for k in keys:
            v = clip.get(k)
            if v is not None: return v
        return None

    return clean({
        "id":         track_id,
        "suno_url":   f"https://suno.com/song/{track_id}",
        "mp3_url":    g("audio_url")       or cdn_mp3,
        "cover_url":  g("image_url")       or cdn_jpg,
        "cover_png":  g("image_large_url") or cdn_png,
        "download": {
            "mp3":       g("audio_url")       or cdn_mp3,
            "cover_jpg": g("image_url")       or cdn_jpg,
            "cover_png": g("image_large_url") or cdn_png,
        },
        "title":        g("title", "display_name"),
        "artist":       g("display_name", "user_display_name", "handle", "author"),
        "tags":         g("tags") or m.get("tags"),
        "genre":        m.get("genre") or m.get("style") or g("style"),
        "duration":     g("duration", "audio_duration"),
        "created_at":   g("created_at"),
        "is_public":    g("is_public"),
        "play_count":   g("play_count"),
        "upvote_count": g("upvote_count"),
        "model":        g("model_version", "model"),
        "prompt":       m.get("prompt") or m.get("gpt_description_prompt") or g("prompt"),
    })

@app.get("/track")
async def track(url: str = Query(...)):
    try:
        tid = await get_id(url)
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:
        return err(f"Failed to resolve: {e}", 500)
    return ok(build(tid, await fetch_meta(tid)))

@app.get("/track/{track_id}")
async def track_by_id(track_id: str):
    if not UUID_RE.fullmatch(track_id):
        return err("Invalid UUID", 400)
    return ok(build(track_id, await fetch_meta(track_id)))
