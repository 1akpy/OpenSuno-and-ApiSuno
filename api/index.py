import asyncio, re, time, httpx
from collections import defaultdict
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

H = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://suno.com/",
}

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
UUID_FIND = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SONG_RE = re.compile(r"suno\.com/song/([0-9a-f-]{36})", re.I)

_cache = {}
_rl = defaultdict(list)

def check_rl(ip):
    now = time.time()
    _rl[ip] = [t for t in _rl[ip] if now - t < 60]
    if len(_rl[ip]) >= 20: return False
    _rl[ip].append(now)
    return True

async def get_id(url):
    url = url.strip()
    if not url.startswith("http"): url = "https://" + url
    if "/s/" in url:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers=H) as c:
            r = await c.get(url)
            url = str(r.url)
    
    m = SONG_RE.search(url)
    if m: return m.group(1)
    m = UUID_FIND.search(url)
    return m.group(0) if m else None

def build_data(tid, meta):
    clip = (meta or {}).get("clip") or meta or {}
    m = clip.get("metadata") or {}
    
    def g(*keys):
        for k in keys:
            val = clip.get(k)
            if val is not None: return val
        return None

    return {
        "id": tid,
        "title": g("title", "display_name"),
        "artist": g("user_display_name", "handle", "author"),
        "mp3_url": g("audio_url") or f"https://cdn1.suno.ai/{tid}.mp3",
        "cover_url": g("image_url") or f"https://cdn2.suno.ai/image_{tid}.jpeg",
        "duration": g("duration", "audio_duration"),
        "prompt": m.get("prompt") or m.get("gpt_description_prompt") or g("prompt"),
        "created_at": g("created_at")
    }

@app.get("/track")
async def track(req: Request, url: str = Query(...)):
    ip = req.headers.get("x-forwarded-for", req.client.host if req.client else "unknown")
    if not check_rl(ip): return JSONResponse({"status": "error", "message": "limit"}, 429)

    if url in _cache and time.time() - _cache[url][1] < 300:
        return {"status": "ok", "data": _cache[url][0]}

    try:
        tid = await get_id(url)
        if not tid: return JSONResponse({"status": "error", "message": "no_id"}, 400)
        
        async with httpx.AsyncClient(timeout=10, headers=H) as c:
            urls = [f"https://studio-api.suno.ai/api/clip/{tid}/", f"https://studio-api.prod.suno.ai/api/clip/{tid}/"]
            res = await asyncio.gather(*[c.get(u) for u in urls], return_exceptions=True)
            meta = next((r.json() for r in res if isinstance(r, httpx.Response) and r.status_code == 200), None)
            
        result = build_data(tid, meta)
        _cache[url] = (result, time.time())
        return {"status": "ok", "data": result}
    except:
        return JSONResponse({"status": "error", "message": "fail"}, 500)

@app.get("/download/{tid}")
async def download(tid: str):
    if not UUID_RE.fullmatch(tid): return JSONResponse({"status": "error", "message": "id"}, 400)
    
    async def stream():
        async with httpx.AsyncClient() as c:
            async with c.stream("GET", f"https://cdn1.suno.ai/{tid}.mp3") as r:
                async for chunk in r.aiter_bytes(8192): yield chunk

    return StreamingResponse(stream(), media_type="audio/mpeg", headers={
        "Content-Disposition": f'attachment; filename="{tid}.mp3"',
        "Access-Control-Allow-Origin": "*"
    })
