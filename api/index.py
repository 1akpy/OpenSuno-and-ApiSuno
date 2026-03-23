from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from urllib.parse import urlparse
import httpx
import re
import asyncio
import time
from collections import defaultdict

# ─────────────────────────────────────────────────────────────────────────────
#  App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

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
BULK_MAX     = 20          # максимум треков в /tracks

UUID_RE      = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
UUID_FIND_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SONG_RE      = re.compile(r"suno\.com/song/([0-9a-f-]{36})", re.I)

# SVG-path сигнатуры счётчиков на странице suno.com/song/<id>
_SIG_LIKE    = "M18.881 8.288"   # 👍  upvotes
_SIG_PLAY    = "M6 18.705"       # ▶   plays / comments / views (три вхождения)

# Парсинг чисел с суффиксом  (1.5K → 1500, 81K → 81000, 2M → 2000000)
_NUM_RE      = re.compile(r"^([\d.]+)([KkMm]?)$")

# ─────────────────────────────────────────────────────────────────────────────
#  Cache  (simple in-memory TTL)
# ─────────────────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[dict, float]] = {}
CACHE_TTL = 300   # секунды


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


# ─────────────────────────────────────────────────────────────────────────────
#  Rate-limiter  (per-IP, sliding window)
# ─────────────────────────────────────────────────────────────────────────────

_rl: dict[str, list[float]] = defaultdict(list)
_rl_last_cleanup = 0.0
RL_MAX    = 20
RL_WINDOW = 60


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


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────

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
    s = re.sub(r'[\x00-\x1f\x7f]', '', str(v).strip())
    return s[:MAX_STR_LEN] if s else None


def safe_num(v, typ=float):
    if v is None:
        return None
    try:
        return typ(v)
    except (TypeError, ValueError):
        return None


def safe_bool(v) -> bool | None:
    if v is None or not isinstance(v, bool):
        return None
    return v


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


def _parse_num(raw: str) -> int | None:
    """'1.5K' → 1500,  '81K' → 81000,  '2M' → 2000000,  '42' → 42"""
    m = _NUM_RE.match(raw.strip())
    if not m:
        return None
    n      = float(m.group(1))
    suffix = m.group(2).upper()
    return int(n * {"K": 1_000, "M": 1_000_000}.get(suffix, 1))


# ─────────────────────────────────────────────────────────────────────────────
#  URL resolution
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
#  Suno API fetch
# ─────────────────────────────────────────────────────────────────────────────

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


async def fetch_api_meta(track_id: str) -> dict | None:
    urls = [
        f"https://studio-api.suno.ai/api/clip/{track_id}/",
        f"https://studio-api.prod.suno.ai/api/clip/{track_id}/",
    ]
    async with httpx.AsyncClient(timeout=6, headers=HEADERS) as c:
        results = await asyncio.gather(*[_try_meta(c, u) for u in urls])
    return next((r for r in results if r is not None), None)


# ─────────────────────────────────────────────────────────────────────────────
#  Page scraping  (лайки / plays / comments / views)
# ─────────────────────────────────────────────────────────────────────────────

# Паттерн: ищем SVG-path → число сразу после закрывающего </svg>
_STAT_RE = re.compile(
    r'd="([^"]{10,200})"'           # атрибут d= у <path>
    r'(?:(?!d=").){0,300}?'         # всё до числа (не захватывая новый path)
    r'</svg>\s*(?:<[^>]+>)?\s*'     # закрываем svg-блок
    r'([\d.,]+[KkMm]?)',            # само число
    re.S,
)


async def fetch_page_stats(track_id: str) -> dict:
    """
    Парсит HTML страницы suno.com/song/<id> и извлекает:
      upvote_count, play_count, comment_count, view_count
    Возвращает пустой dict если ничего не нашлось.
    """
    url = f"https://suno.com/song/{track_id}"
    try:
        async with httpx.AsyncClient(timeout=8, headers=HEADERS) as c:
            r = await c.get(url, follow_redirects=True)
        if r.status_code != 200:
            return {}
    except Exception:
        return {}

    html      = r.text
    stats: dict[str, int | None] = {}
    seen_play = 0

    for m in _STAT_RE.finditer(html):
        path_d  = m.group(1)
        raw_val = m.group(2)
        val     = _parse_num(raw_val)

        if _SIG_LIKE in path_d:
            stats["upvote_count"] = val

        elif _SIG_PLAY in path_d:
            seen_play += 1
            if seen_play == 1:
                stats["play_count"]    = val
            elif seen_play == 2:
                stats["comment_count"] = val
            elif seen_play == 3:
                stats["view_count"]    = val

    return stats


# ─────────────────────────────────────────────────────────────────────────────
#  Combine everything into one unified fetch
# ─────────────────────────────────────────────────────────────────────────────

async def fetch_meta(track_id: str) -> tuple[dict | None, dict]:
    """
    Параллельно запрашивает JSON API и парсит страницу.
    Возвращает (api_meta | None, page_stats).
    """
    api_meta, page_stats = await asyncio.gather(
        fetch_api_meta(track_id),
        fetch_page_stats(track_id),
    )
    return api_meta, page_stats


# ─────────────────────────────────────────────────────────────────────────────
#  Build response object
# ─────────────────────────────────────────────────────────────────────────────

def build(track_id: str, meta: dict | None, page_stats: dict) -> dict:
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
    title     = safe_str(g("title"))           or safe_str(g("display_name"))
    artist    = safe_str(g("user_display_name") or g("handle") or g("author"))

    # Статистика: page_stats в приоритете над API (свежее и полнее)
    play_count    = page_stats.get("play_count")    or safe_num(g("play_count"),    int)
    upvote_count  = page_stats.get("upvote_count")  or safe_num(g("upvote_count"),  int)
    comment_count = page_stats.get("comment_count")   # только из страницы
    view_count    = page_stats.get("view_count")       # только из страницы

    return clean({
        "id":       track_id,
        "suno_url": f"https://suno.com/song/{track_id}",

        "mp3_url":   mp3_url,
        "cover_url": cover_jpg,
        "cover_png": cover_png,

        "download": {
            "mp3":       mp3_url,
            "cover_jpg": cover_jpg,
            "cover_png": cover_png,
        },

        "title":    title,
        "artist":   artist,
        "tags":     safe_str(g("tags") or m.get("tags")),
        "genre":    safe_str(m.get("genre") or m.get("style") or g("style")),
        "prompt":   safe_str(m.get("prompt") or m.get("gpt_description_prompt") or g("prompt")),
        "model":    safe_str(g("model_version", "model")),

        "duration":   safe_num(g("duration", "audio_duration"), float),
        "created_at": safe_str(g("created_at")),
        "is_public":  safe_bool(g("is_public")),

        "stats": clean({
            "play_count":    play_count,
            "upvote_count":  upvote_count,
            "comment_count": comment_count,
            "view_count":    view_count,
        }),
    })


# ─────────────────────────────────────────────────────────────────────────────
#  Shared logic: resolve URL → fetch → build (с кешем)
# ─────────────────────────────────────────────────────────────────────────────

async def _resolve_and_build(url: str) -> dict:
    """Полный пайплайн для одного трека. Выбрасывает ValueError при ошибке."""
    cached = cache_get(f"url:{url}")
    if cached:
        return cached

    tid = await get_id(url)

    cached = cache_get(tid)
    if cached:
        cache_set(f"url:{url}", cached)
        return cached

    api_meta, page_stats = await fetch_meta(tid)
    result = build(tid, api_meta, page_stats)
    cache_set(tid, result)
    cache_set(f"url:{url}", result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
#  Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/track")
async def track(request: Request, url: str = Query(...)):
    """
    GET /track?url=<suno_url>

    Возвращает метаданные + статистику одного трека.
    """
    ip = get_real_ip(request)
    if not check_rate_limit(ip):
        return err("Rate limit exceeded — max 20 requests per minute", 429)
    if not url or len(url) > MAX_URL_LEN:
        return err("URL too long or empty", 400)

    try:
        result = await _resolve_and_build(url)
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:
        return err(f"Failed to resolve: {e}", 500)

    return ok(result)


@app.get("/tracks")
async def tracks(request: Request, url: list[str] = Query(...)):
    """
    GET /tracks?url=<url1>&url=<url2>&...

    Bulk-запрос: до 20 треков параллельно.
    Каждый элемент ответа содержит либо данные, либо ошибку.
    """
    ip = get_real_ip(request)
    if not check_rate_limit(ip):
        return err("Rate limit exceeded — max 20 requests per minute", 429)
    if len(url) > BULK_MAX:
        return err(f"Too many URLs — max {BULK_MAX} per request", 400)

    async def _safe(u: str) -> dict:
        if not u or len(u) > MAX_URL_LEN:
            return {"url": u, "status": "error", "message": "URL too long or empty"}
        try:
            data = await _resolve_and_build(u)
            return {"url": u, "status": "ok", "data": data}
        except ValueError as e:
            return {"url": u, "status": "error", "message": str(e)}
        except Exception as e:
            return {"url": u, "status": "error", "message": f"Failed: {e}"}

    results = await asyncio.gather(*[_safe(u) for u in url])
    return JSONResponse({"status": "ok", "data": list(results)})


@app.get("/download/{track_id}")
async def download(request: Request, track_id: str):
    """
    GET /download/<uuid>

    Стриминг MP3 напрямую с CDN Suno.
    """
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
            },
        )
    except Exception as e:
        return err(f"Download failed: {e}", 500)
