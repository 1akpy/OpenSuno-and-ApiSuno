from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
import httpx
import re

app = FastAPI(title="SunoAPI", docs_url="/docs")

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

# ─── helpers ──────────────────────────────────────────────────────────────────

def ok(data):
    return JSONResponse({"status": "ok", "data": data})

def err(msg: str, code: int = 400):
    return JSONResponse({"status": "error", "message": msg, "data": None}, status_code=code)

def clean(d: dict) -> dict:
    """Remove keys with None values."""
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
    cdn_mp3  = f"https://cdn1.suno.ai/{track_id}.mp3"
    cdn_jpg  = f"https://cdn2.suno.ai/image_{track_id}.jpeg"
    cdn_png  = f"https://cdn2.suno.ai/image_{track_id}.png"

    clip = (meta or {}).get("clip") or meta or {}

    def g(*keys):
        for k in keys:
            v = clip.get(k)
            if v is not None: return v
        return None

    meta_dict = clip.get("metadata") or {}

    data = {
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
        "tags":         g("tags") or meta_dict.get("tags"),
        "genre":        meta_dict.get("genre") or meta_dict.get("style") or g("style"),
        "duration":     g("duration", "audio_duration"),
        "created_at":   g("created_at"),
        "is_public":    g("is_public"),
        "play_count":   g("play_count"),
        "upvote_count": g("upvote_count"),
        "model":        g("model_version", "model"),
        "prompt":       meta_dict.get("prompt") or meta_dict.get("gpt_description_prompt") or g("prompt"),
    }

    return clean(data)  # ← removes all None fields

# ─── routes ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def homepage():
    return HTMLResponse(HTML_PAGE)

@app.get("/track")
async def track(url: str = Query(..., description="Any Suno URL")):
    try:
        tid = await get_id(url)
    except ValueError as e:
        return err(str(e), 400)
    except Exception as e:
        return err(f"Failed to resolve: {e}", 500)
    meta = await fetch_meta(tid)
    return ok(build(tid, meta))

@app.get("/track/{track_id}")
async def track_by_id(track_id: str):
    if not UUID_RE.fullmatch(track_id):
        return err("Invalid UUID", 400)
    meta = await fetch_meta(track_id)
    return ok(build(track_id, meta))

# ─── HTML page ────────────────────────────────────────────────────────────────

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SunoAPI</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:#0a0a0a;color:#fff;font-family:system-ui,sans-serif;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:2rem 1rem}
  h1{font-size:2rem;font-weight:800;letter-spacing:-.04em;margin-bottom:.3rem}
  h1 span{color:#fb923c}
  .sub{color:#555;font-size:.8rem;margin-bottom:2rem}
  .box{width:100%;max-width:480px}
  .row{display:flex;background:#111;border:1px solid #222;border-radius:12px;overflow:hidden;margin-bottom:.5rem}
  .row:focus-within{border-color:#fb923c44}
  input{flex:1;background:transparent;border:none;outline:none;padding:.85rem 1rem;color:#fff;font-size:.85rem;font-family:inherit}
  input::placeholder{color:#444}
  button.go{background:#fb923c;border:none;padding:.75rem 1.2rem;color:#000;font-weight:700;font-size:.82rem;cursor:pointer;transition:background .15s}
  button.go:hover{background:#f97316}
  button.go:disabled{opacity:.4;cursor:default}

  .card{background:#111;border:1px solid #1f1f1f;border-radius:14px;overflow:hidden;display:none;margin-top:.8rem}
  .card.show{display:block}
  .card-top{display:flex;gap:1rem;padding:1rem}
  .cover{width:72px;height:72px;border-radius:8px;object-fit:cover;background:#1a1a1a;flex-shrink:0}
  .meta{flex:1;min-width:0}
  .meta h2{font-size:.95rem;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .meta p{font-size:.75rem;color:#666;margin-top:.2rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .badge{display:inline-flex;align-items:center;gap:.3rem;font-size:.6rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;background:#fb923c18;border:1px solid #fb923c33;color:#fb923c;border-radius:999px;padding:.15rem .5rem;margin-top:.35rem}

  audio{width:100%;margin:0;display:block}
  audio::-webkit-media-controls-panel{background:#111}

  .btns{display:flex;gap:.5rem;padding:.8rem 1rem 1rem}
  .dl{background:#1a1a1a;border:1px solid #2a2a2a;color:#ccc;font-family:inherit;font-size:.72rem;font-weight:600;padding:.4rem .9rem;border-radius:999px;cursor:pointer;transition:background .15s,color .15s}
  .dl:hover{background:#252525;color:#fff}

  .toast{font-size:.72rem;color:#f55;margin-top:.4rem;min-height:1em}

  .docs{margin-top:2.5rem;font-size:.7rem;color:#333;text-align:center;line-height:2}
  .docs code{background:#151515;border:1px solid #222;padding:.1rem .4rem;border-radius:4px;color:#fb923c;font-size:.68rem}
  a{color:#fb923c;text-decoration:none}
</style>
</head>
<body>

<div class="box">
  <h1>Suno<span>API</span></h1>
  <p class="sub">Unofficial Suno track resolver &mdash; free &amp; open</p>

  <div class="row">
    <input id="inp" placeholder="suno.com/song/... or suno.com/s/..." >
    <button class="go" id="btn" onclick="load()">Load</button>
  </div>
  <div class="toast" id="toast"></div>

  <div class="card" id="card">
    <div class="card-top">
      <img class="cover" id="cover" src="" alt="">
      <div class="meta">
        <h2 id="title">—</h2>
        <p id="artist">—</p>
        <span class="badge">&#9654; suno track</span>
      </div>
    </div>
    <audio id="player" controls></audio>
    <div class="btns">
      <button class="dl" onclick="dlMp3()">⬇ MP3</button>
      <button class="dl" onclick="dlCover('jpg')">⬇ Cover JPG</button>
      <button class="dl" onclick="dlCover('png')">⬇ Cover PNG</button>
    </div>
  </div>

  <div class="docs">
    <b style="color:#555">API endpoints</b><br>
    <code>GET /track?url=suno.com/s/xxx</code><br>
    <code>GET /track?url=suno.com/song/uuid</code><br>
    <code>GET /track/{uuid}</code><br>
    <a href="/docs">Swagger UI &rarr;</a>
  </div>
</div>

<script>
let data = null;

async function load() {
  const url = document.getElementById('inp').value.trim();
  if (!url) return;
  const btn = document.getElementById('btn');
  btn.disabled = true; btn.textContent = '...';
  document.getElementById('toast').textContent = '';
  document.getElementById('card').classList.remove('show');

  try {
    const r = await fetch('/track?url=' + encodeURIComponent(url));
    const j = await r.json();
    if (j.status !== 'ok') throw new Error(j.message);
    data = j.data;

    document.getElementById('cover').src   = data.cover_url || '';
    document.getElementById('title').textContent  = data.title  || data.id;
    document.getElementById('artist').textContent = data.artist || 'suno.com';
    document.getElementById('player').src  = data.mp3_url;
    document.getElementById('card').classList.add('show');
  } catch(e) {
    document.getElementById('toast').textContent = e.message || 'error';
  } finally {
    btn.disabled = false; btn.textContent = 'Load';
  }
}

async function dlFile(url, name) {
  try {
    const r = await fetch(url);
    const b = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b); a.download = name;
    a.click();
  } catch { window.open(url, '_blank'); }
}

function dlMp3()       { if (data) dlFile(data.download.mp3,       (data.title||data.id)+'.mp3'); }
function dlCover(ext)  { if (data) dlFile(data.download['cover_'+ext], (data.title||data.id)+'_cover.'+ext); }

document.getElementById('inp').addEventListener('keydown', e => { if (e.key==='Enter') load(); });
</script>
</body>
</html>"""
