from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from urllib.parse import urlparse
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://suno.com/",
}

ALLOWED_CDN  = ("https://cdn1.suno.ai/", "https://cdn2.suno.ai/")
SUNO_HOSTS   = {"suno.com", "www.suno.com", "studio-api.suno.ai", "studio-api.prod.suno.ai"}
MAX_URL_LEN  = 512
MAX_STR_LEN  = 512
UUID_RE      = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
UUID_FIND_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SONG_RE      = re.compile(r"suno\.com/song/([0-9a-f-]{36})", re.I)

_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 300

_rl: dict[str, list[float]] = defaultdict(list)
_rl_last_cleanup = 0.0
RL_MAX    = 20
RL_WINDOW = 60


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


def _cleanup_rl() -> None:
    global _rl_last_cleanup
    now = time.time()
    if now - _rl_last_cleanup < 120:
        return
    _rl_last_cleanup = now
    stale = [ip for ip, hits in _rl.items() if all(now - t >= RL_WINDOW for t in hits)]
    for ip in stale:
        del _rl[ip]


def get_real_ip(request: Request) -> str:
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


def ok(data):
    return JSONResponse({"status": "ok", "data": data})


def err(msg: str, code: int = 400):
    return JSONResponse({"status": "error", "message": msg, "data": None}, status_code=code)


def clean(d: dict) -> dict:
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            nested = clean(v)
            if nested:
                result[k] = nested
        elif v is not None:
            result[k] = v
    return result


def safe_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    s = re.sub(r'[\x00-\x1f\x7f]', '', s)
    return s[:MAX_STR_LEN] if s else None


def safe_num(v, typ=float):
    if v is None:
        return None
    try:
        return typ(v)
    except (TypeError, ValueError):
        return None


def safe_bool(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return None


def safe_cdn(url) -> str | None:
    if url and isinstance(url, str) and any(url.startswith(c) for c in ALLOWED_CDN):
        return url[:1024]
    return None


def is_suno_host(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        return host in SUNO_HOSTS or any(host.endswith("." + h) for h in SUNO_HOSTS)
    except Exception:
        return False


def extract_id(url: str) -> str | None:
    m = SONG_RE.search(url)
    if m:
        return m.group(1)
    m = UUID_FIND_RE.search(url)
    if m:
        return m.group(0)
    return None


async def resolve_short(url: str) -> str:
    async with httpx.AsyncClient(timeout=8, follow_redirects=False, headers=HEADERS) as c:
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


async def _try_meta(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
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


async def fetch_comments(track_id: str) -> int | None:
    urls = [
        f"https://studio-api.suno.ai/api/comment/?clip_id={track_id}&page=0",
        f"https://studio-api.prod.suno.ai/api/comment/?clip_id={track_id}&page=0",
    ]
    async with httpx.AsyncClient(timeout=6, headers=HEADERS) as c:
        results = await asyncio.gather(*[_try_meta(c, u) for u in urls])
    for r in results:
        if r is None:
            continue
        for key in ("total_num", "total", "count", "total_count"):
            v = r.get(key)
            if v is not None:
                return safe_num(v, int)
        if isinstance(r, list):
            return len(r)
        if isinstance(r.get("comments"), list):
            return len(r["comments"])
    return None


def build(track_id: str, meta: dict | None, comment_count: int | None = None) -> dict:
    cdn_mp3 = f"https://cdn1.suno.ai/{track_id}.mp3"
    cdn_jpg = f"https://cdn2.suno.ai/image_{track_id}.jpeg"
    cdn_png = f"https://cdn2.suno.ai/image_{track_id}.png"
    clip    = (meta or {}).get("clip") or meta or {}
    m       = clip.get("metadata") or {}

    def g(*keys):
        for k in keys:
            v = clip.get(k)
            if v is not None:
                return v
        return None

    mp3_url   = safe_cdn(g("audio_url"))       or cdn_mp3
    cover_jpg = safe_cdn(g("image_url"))       or cdn_jpg
    cover_png = safe_cdn(g("image_large_url")) or cdn_png

    title  = safe_str(g("title")) or safe_str(g("display_name"))
    artist = safe_str(g("user_display_name") or g("handle") or g("author"))

    return clean({
        "id":            track_id,
        "suno_url":      f"https://suno.com/song/{track_id}",
        "mp3_url":       mp3_url,
        "cover_url":     cover_jpg,
        "cover_png":     cover_png,
        "download": {
            "mp3":       mp3_url,
            "cover_jpg": cover_jpg,
            "cover_png": cover_png,
        },
        "title":         title,
        "artist":        artist,
        "tags":          safe_str(g("tags") or m.get("tags")),
        "genre":         safe_str(m.get("genre") or m.get("style") or g("style")),
        "duration":      safe_num(g("duration", "audio_duration"), float),
        "created_at":    safe_str(g("created_at")),
        "is_public":     safe_bool(g("is_public")),
        "play_count":    safe_num(g("play_count"), int),
        "upvote_count":  safe_num(g("upvote_count"), int),
        "comment_count": comment_count,
        "model":         safe_str(g("model_version", "model")),
        "prompt":        safe_str(m.get("prompt") or m.get("gpt_description_prompt") or g("prompt")),
    })


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

    meta, comments = await asyncio.gather(fetch_meta(tid), fetch_comments(tid))
    result = build(tid, meta, comments)
    cache_set(tid, result)
    cache_set(f"url:{url}", result)
    return ok(result)


@app.get("/stats")
async def stats(request: Request, url: str = Query(...)):
    ip = get_real_ip(request)
    if not check_rate_limit(ip):
        return err("Rate limit exceeded — max 20 requests per minute", 429)
    if not url or len(url) > MAX_URL_LEN:
        return err("URL too long or empty", 400)

    cached = cache_get(f"stats:{url}")
    if cached:
        return ok(cached)

    try:
        tid = await get_id(url)
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:
        return err(f"Failed to resolve: {e}", 500)

    meta, comments = await asyncio.gather(fetch_meta(tid), fetch_comments(tid))
    clip = (meta or {}).get("clip") or meta or {}

    def g(*keys):
        for k in keys:
            v = clip.get(k)
            if v is not None:
                return v
        return None

    result = clean({
        "id":            tid,
        "suno_url":      f"https://suno.com/song/{tid}",
        "artist":        safe_str(g("user_display_name", "handle", "author")),
        "play_count":    safe_num(g("play_count"), int),
        "upvote_count":  safe_num(g("upvote_count"), int),
        "comment_count": comments,
    })

    cache_set(f"stats:{url}", result)
    return ok(result)


@app.get("/download/{track_id}")
async def download(request: Request, track_id: str):
    ip = get_real_ip(request)
    if not check_rate_limit(ip):
        return err("Rate limit exceeded", 429)
    if not UUID_RE.fullmatch(track_id):
        return err("Invalid UUID format", 400)

    mp3_url = f"https://cdn1.suno.ai/{track_id}.mp3"

    try:
        async def stream():
            async with httpx.AsyncClient(timeout=60, headers=HEADERS) as c:
                async with c.stream("GET", mp3_url) as r:
                    if r.status_code != 200:
                        return
                    async for chunk in r.aiter_bytes(chunk_size=8192):
                        yield chunk

        return StreamingResponse(
            stream(),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f'attachment; filename="{track_id}.mp3"',
                "Access-Control-Allow-Origin": "*",
            }
        )
    except Exception as e:
        return err(f"Download failed: {e}", 500)
