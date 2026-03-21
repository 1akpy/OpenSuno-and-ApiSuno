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

ALLOWED_CDN  = ("https://cdn1.suno.ai/", "https://cdn2.suno.ai/")
SUNO_HOSTS   = ("suno.com", "www.suno.com", "studio-api.suno.ai", "studio-api.prod.suno.ai")
MAX_URL_LEN  = 512
MAX_STR_LEN  = 512   # max length for any string field from meta
UUID_RE      = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
UUID_FIND_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SONG_RE      = re.compile(r"suno\.com/song/([0-9a-f-]{36})", re.I)

# ─── cache (TTL 5 min) ────────────────────────────────────────────────────────

_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 300

def cache_get(key: str) -> dict | None:
    entry = _cache.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    if entry:
        del _cache[key]
    return None

def cache_set(key: str, value: dict) -> None:
    if len(_cache) >= 500:
        oldest = min(_cache, key=lambda k: _cache[k][1])
        del _cache[oldest]
    _cache[key] = (value, time.time() + CACHE_TTL)

# ─── rate limiter (per IP, 20 req / 60s) ─────────────────────────────────────

_rl: dict[str, list[float]] = defaultdict(list)
_rl_last_cleanup = 0.0
RL_MAX    = 20
RL_WINDOW = 60

def _cleanup_rl() -> None:
    """Periodically remove stale IPs to prevent memory leak."""
    global _rl_last_cleanup
    now = time.time()
    if now - _rl_last_cleanup < 120:   # cleanup every 2 min
        return
    _rl_last_cleanup = now
    stale = [ip for ip, hits in _rl.items() if all(now - t >= RL_WINDOW for t in hits)]
    for ip in stale:
        del _rl[ip]

def get_real_ip(request: Request) -> str:
    """
    On Vercel, request.client.host is always the proxy IP.
    Read X-Forwarded-For to get the real client IP.
    Take only the FIRST value — that's the original client.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def check_rate_limit(ip: str) -> bool:
    _cleanup_rl()
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

def safe_str(v) -> str | None:
    """Sanitize a string field from untrusted meta — truncate, strip control chars."""
    if v is None:
        return None
    s = str(v).strip()
    # remove control characters
    s = re.sub(r'[\x00-\x1f\x7f]', '', s)
    return s[:MAX_STR_LEN] if s else None

def safe_cdn(url) -> str | None:
    """Return url only if it starts with an allowed Suno CDN prefix."""
    if url and isinstance(url, str) and any(url.startswith(c) for c in ALLOWED_CDN):
        return url[:1024]  # cap length just in case
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
    """Follow suno.com/s/xxx — manual redirect, SSRF-safe."""
    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=False,
        headers=HEADERS,
    ) as c:
        for _ in range(5):
            r = await c.get(url)
            if r.status_code in (301, 302, 303, 307, 308):
                location = r.headers.get("location", "")
                if not location:
                    break
                if not location.startswith("http"):
                    location = "https://suno.com" + location
                if not is_suno_host(location):
                    raise ValueError("Redirect to non-Suno host blocked")
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
            raise ValueError("Could not extract ID from resolved URL")
        return tid
    tid = extract_id(raw)
    if not tid:
        raise ValueError("No track ID found in URL")
    return tid

# ─── metadata fetch (parallel) ───────────────────────────────────────────────

async def _try_meta(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        # check content-type before parsing JSON
        ct = r.headers.get("content-type", "")
        if "json" not in ct and "javascript" not in ct:
            return None
        return r.json()
    except Exception:
        return None

async def fetch_meta(track_id: str) -> dict | None:
    urls = [
        f"https://studio-api.suno.ai/api/clip/{track_id}/",
        f"https://studio-api.prod.suno.ai/api/clip/{track_id}/",
    ]
    async with httpx.AsyncClient(timeout=6, headers=HEADERS) as c:
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

    mp3_url   = safe_cdn(g("audio_url"))       or cdn_mp3
    cover_jpg = safe_cdn(g("image_url"))       or cdn_jpg
    cover_png = safe_cdn(g("image_large_url")) or cdn_png

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
        # all string fields sanitized
        "title":        safe_str(g("title", "display_name")),
        "artist":       safe_str(g("display_name", "user_display_name", "handle", "author")),
        "tags":         safe_str(g("tags") or m.get("tags")),
        "genre":        safe_str(m.get("genre") or m.get("style") or g("style")),
        "duration":     g("duration", "audio_duration"),
        "created_at":   safe_str(g("created_at")),
        "is_public":    g("is_public"),
        "play_count":   g("play_count"),
        "upvote_count": g("upvote_count"),
        "model":        safe_str(g("model_version", "model")),
        "prompt":       safe_str(m.get("prompt") or m.get("gpt_description_prompt") or g("prompt")),
    })

# ─── routes ───────────────────────────────────────────────────────────────────

@app.get("/track")
async def track(request: Request, url: str = Query(...)):
    ip = get_real_ip(request)
    if not check_rate_limit(ip):
        return err("Rate limit exceeded — max 20 requests per minute", 429)
    if not url or len(url) > MAX_URL_LEN:
        return err("URL too long or empty", 400)

    cached = cache_get(f"url:{url}")
    if cached:
        return ok(cached)

    try:
        tid = await get_id(url)
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:
        return err(f"Failed to resolve: {e}", 500)

    cached = cache_get(tid)
    if cached:
        cache_set(f"url:{url}", cached)
        return ok(cached)

    meta   = await fetch_meta(tid)
    result = build(tid, meta)
    cache_set(tid, result)
    cache_set(f"url:{url}", result)
    return ok(result)


@app.get("/track/{track_id}")
async def track_by_id(request: Request, track_id: str):
    ip = get_real_ip(request)
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
