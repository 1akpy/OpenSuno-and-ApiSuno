from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx
import re
import asyncio
import time
from collections import defaultdict

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ─── constants ────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://suno.com/",
}

ALLOWED_CDN   = ("https://cdn1.suno.ai/", "https://cdn2.suno.ai/")
SUNO_HOSTS    = ("suno.com", "www.suno.com", "studio-api.suno.ai", "studio-api.prod.suno.ai")
MAX_URL_LEN   = 512
UUID_RE       = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
UUID_FIND_RE  = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SONG_RE       = re.compile(r"suno\.com/song/([0-9a-f-]{36})", re.I)

# ─── simple in-memory cache (TTL 5 min) ──────────────────────────────────────

_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 300  # seconds

def cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    if entry:
        del _cache[key]
    return None

def cache_set(key: str, value: dict) -> None:
    # keep cache small — evict oldest if over 500 entries
    if len(_cache) >= 500:
        oldest = min(_cache, key=lambda k: _cache[k][1])
        del _cache[oldest]
    _cache[key] = (value, time.time() + CACHE_TTL)

# ─── in-memory rate limiter (per IP, 20 req / 60s) ───────────────────────────

_rl: dict[str, list[float]] = defaultdict(list)
RL_MAX    = 20
RL_WINDOW = 60

def check_rate_limit(ip: str) -> bool:
    now  = time.time()
    hits = [t for t in _rl[ip] if now - t < RL_WINDOW]
    _rl[ip] = hits
    if len(hits) >= RL_MAX:
        return False
    _rl[ip].append(now)
    return True

# ─── helpers ──────────────────────────────────────────────────────────────────

def ok(data):
    return JSONResponse({"status": "ok", "data": data})

def err(msg: str, code: int = 400):
    return JSONResponse({"status": "error", "message": msg, "data": None}, status_code=code)

def clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}

def safe_cdn(url: str | None) -> str | None:
    """Return url only if it points to Suno CDN, else None."""
    if url and isinstance(url, str) and any(url.startswith(c) for c in ALLOWED_CDN):
        return url
    return None

def is_suno_host(url: str) -> bool:
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        return any(host == h or host.endswith("." + h) for h in SUNO_HOSTS)
    except Exception:
        return False

# ─── URL parsing ──────────────────────────────────────────────────────────────

def extract_id(url: str) -> str | None:
    m = SONG_RE.search(url)
    if m: return m.group(1)
    m = UUID_FIND_RE.search(url)
    if m: return m.group(0)
    return None

async def resolve_short(url: str) -> str:
    """Follow suno.com/s/xxx redirect — only allow redirects within suno.com."""
    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=False,   # manual redirect to prevent SSRF
        headers=HEADERS,
    ) as c:
        for _ in range(5):        # max 5 hops
            r = await c.get(url)
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("location", "")
                if not location:
                    break
                if not location.startswith("http"):
                    location = "https://suno.com" + location
                if not is_suno_host(location):
                    raise ValueError(f"Redirect to non-Suno host blocked: {location}")
                url = location
            else:
                return str(r.url)
    return url

async def get_id(raw: str) -> str:
    raw = raw.strip()
    if len(raw) > MAX_URL_LEN:
        raise ValueError("URL too long")
    if not raw.startswith("http"):
        raw = "https://" + raw
    if not is_suno_host(raw):
        raise ValueError("Only suno.com links are supported")
    if "/s/" in raw:
        resolved = await resolve_short(raw)
        tid = extract_id(resolved)
        if not tid:
            raise ValueError(f"Could not extract ID from resolved URL")
        return tid
    tid = extract_id(raw)
    if not tid:
        raise ValueError("No track ID found in URL")
    return tid

# ─── metadata fetch (parallel) ───────────────────────────────────────────────

async def _try_meta(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await client.get(url)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

async def fetch_meta(track_id: str) -> dict | None:
    urls = [
        f"https://studio-api.suno.ai/api/clip/{track_id}/",
        f"https://studio-api.prod.suno.ai/api/clip/{track_id}/",
    ]
    async with httpx.AsyncClient(timeout=6, headers=HEADERS) as c:
        # fire both in parallel, return first success
        results = await asyncio.gather(*[_try_meta(c, u) for u in urls])
    return next((r for r in results if r is not None), None)

# ─── response builder ─────────────────────────────────────────────────────────

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

    # validate CDN urls from meta — fallback to constructed if unsafe
    mp3_url    = safe_cdn(g("audio_url"))        or cdn_mp3
    cover_jpg  = safe_cdn(g("image_url"))        or cdn_jpg
    cover_png  = safe_cdn(g("image_large_url"))  or cdn_png

    return clean({
        "id":         track_id,
        "suno_url":   f"https://suno.com/song/{track_id}",
        "mp3_url":    mp3_url,
        "cover_url":  cover_jpg,
        "cover_png":  cover_png,
        "download": {
            "mp3":       mp3_url,
            "cover_jpg": cover_jpg,
            "cover_png": cover_png,
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

# ─── routes ───────────────────────────────────────────────────────────────────

@app.get("/track")
async def track(request: Request, url: str = Query(...)):
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip):
        return err("Rate limit exceeded — max 20 requests per minute", 429)

    if not url or len(url) > MAX_URL_LEN:
        return err("URL too long or empty", 400)

    # check cache first
    cached = cache_get(f"url:{url}")
    if cached:
        return ok(cached)

    try:
        tid = await get_id(url)
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:
        return err(f"Failed to resolve: {e}", 500)

    # check cache by id
    cached = cache_get(tid)
    if cached:
        return ok(cached)

    meta   = await fetch_meta(tid)
    result = build(tid, meta)
    cache_set(tid, result)
    cache_set(f"url:{url}", result)
    return ok(result)


@app.get("/track/{track_id}")
async def track_by_id(request: Request, track_id: str):
    ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(ip):
        return err("Rate limit exceeded — max 20 requests per minute", 429)

    if not UUID_RE.fullmatch(track_id):
        return err("Invalid UUID format", 400)

    cached = cache_get(track_id)
    if cached:
        return ok(cached)

    meta   = await fetch_meta(track_id)
    result = build(track_id, meta)
    cache_set(track_id, result)
    return ok(result)
