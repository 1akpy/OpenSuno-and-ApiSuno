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

# ─── response helpers ─────────────────────────────────────────────────────────

def ok(data):
    return JSONResponse({"status": "ok", "data": data})

def err(msg: str, code: int = 400):
    return JSONResponse({"status": "error", "message": msg, "data": None}, status_code=code)

# ─── ID extraction ────────────────────────────────────────────────────────────

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SHORT_RE = re.compile(r"suno\.com/s/([A-Za-z0-9_-]+)")
SONG_RE  = re.compile(r"suno\.com/song/([0-9a-f-]+)", re.I)

def extract_id_from_url(url: str) -> str | None:
    """Try to pull a UUID from a full suno URL directly."""
    m = SONG_RE.search(url)
    if m:
        return m.group(1)
    m = UUID_RE.search(url)
    if m:
        return m.group(0)
    return None

async def resolve_short_url(short_url: str) -> str:
    """Follow suno.com/s/xxx redirect and return the final URL."""
    async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers=HEADERS) as client:
        r = await client.get(short_url)
        return str(r.url)

async def get_track_id(raw: str) -> str:
    """Accept any suno link (short or full) and return the UUID."""
    raw = raw.strip()

    # make sure it has a scheme
    if not raw.startswith("http"):
        raw = "https://" + raw

    # short link → resolve
    if "/s/" in raw:
        resolved = await resolve_short_url(raw)
        track_id = extract_id_from_url(resolved)
        if not track_id:
            raise ValueError(f"Could not extract ID from resolved URL: {resolved}")
        return track_id

    # full link
    track_id = extract_id_from_url(raw)
    if not track_id:
        raise ValueError("No track ID found in URL")
    return track_id

# ─── Suno CDN urls ────────────────────────────────────────────────────────────

def build_track_data(track_id: str) -> dict:
    return {
        "id":          track_id,
        "mp3_url":     f"https://cdn1.suno.ai/{track_id}.mp3",
        "cover_url":   f"https://cdn2.suno.ai/image_{track_id}.jpeg",
        "cover_png":   f"https://cdn2.suno.ai/image_{track_id}.png",
        "stream_url":  f"https://cdn1.suno.ai/{track_id}.mp3",
        "suno_url":    f"https://suno.com/song/{track_id}",
        "download": {
            "mp3":       f"https://cdn1.suno.ai/{track_id}.mp3",
            "cover_jpg": f"https://cdn2.suno.ai/image_{track_id}.jpeg",
            "cover_png": f"https://cdn2.suno.ai/image_{track_id}.png",
        }
    }

# ─── routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "status": "ok",
        "name":   "SunoAPI",
        "usage":  "GET /track?url=suno.com/song/... or suno.com/s/...",
        "docs":   "/docs",
    }

@app.get("/track")
async def track(url: str = Query(..., description="Any Suno track URL — short or full")):
    """
    Resolve any Suno link and return MP3, cover and metadata URLs.

    Accepts:
    - https://suno.com/song/453a796e-a8e2-4d28-b24f-40f956cb5321?sh=...
    - https://suno.com/s/r4t4FIFyoU7GTnX8
    - suno.com/s/r4t4FIFyoU7GTnX8  (no https — also fine)
    """
    try:
        track_id = await get_track_id(url)
        data = build_track_data(track_id)
        return ok(data)
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:
        return err(f"Server error: {str(e)}", 500)


@app.get("/track/{track_id}")
async def track_by_id(track_id: str):
    """
    Get track data directly by UUID.

    Example: /track/453a796e-a8e2-4d28-b24f-40f956cb5321
    """
    if not UUID_RE.fullmatch(track_id):
        return err("Invalid track ID format — must be a UUID", 400)
    return ok(build_track_data(track_id))
