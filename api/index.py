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

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://suno.com/"}
UR = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
SR = re.compile(r"suno\.com/song/([0-9a-f-]{36})", re.I)

_c, _rl = {}, defaultdict(list)

def check_rl(ip):
    now = time.time()
    _rl[ip] = [t for t in _rl[ip] if now - t < 60]
    if len(_rl[ip]) >= 30: return False
    _rl[ip].append(now)
    return True

async def get_id(url):
    if "/s/" in url:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            url = str((await c.head(url)).url)
    m = SR.search(url) or UR.search(url)
    return m.group(1) if hasattr(m, 'group') and m.lastindex else (m.group(0) if m else None)

@app.get("/track")
async def track(req: Request, url: str = Query(...)):
    ip = req.headers.get("x-forwarded-for", req.client.host)
    if not check_rl(ip): return JSONResponse({"err": "limit"}, 429)

    if url in _c and time.time() - _c[url][1] < 300: return _c[url][0]

    tid = await get_id(url)
    if not tid: return JSONResponse({"err": "no_id"}, 400)

    async with httpx.AsyncClient(timeout=10, headers=H) as c:
        for u in [f"https://studio-api.suno.ai/api/clip/{tid}/", f"https://studio-api.prod.suno.ai/api/clip/{tid}/"]:
            try:
                r = await c.get(u)
                if r.status_code == 200:
                    data = r.json()
                    clip = data.get("clip") or data
                    res = {
                        "id": tid,
                        "title": clip.get("title"),
                        "mp3": clip.get("audio_url") or f"https://cdn1.suno.ai/{tid}.mp3",
                        "img": clip.get("image_url")
                    }
                    _c[url] = (res, time.time())
                    return res
            except: continue
    return JSONResponse({"err": "fail"}, 404)

@app.get("/download/{tid}")
async def dl(tid: str):
    if not UR.fullmatch(tid): return JSONResponse({"err": "id"}, 400)
    
    async def stream():
        async with httpx.AsyncClient() as c:
            async with c.stream("GET", f"https://cdn1.suno.ai/{tid}.mp3") as r:
                async for chunk in r.aiter_bytes(16384): yield chunk

    return StreamingResponse(stream(), media_type="audio/mpeg", headers={
        "Content-Disposition": f'attachment; filename="{tid}.mp3"',
        "Access-Control-Allow-Origin": "*"
    })
