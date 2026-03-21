# SunoAPI 🎵

Unofficial Suno track resolver — pass any Suno link, get back MP3, cover and metadata URLs.

Free to use. Deploy your own instance in 2 minutes.

---

## Endpoints

### `GET /track?url=<suno_link>`

Accepts **any** Suno link format:

```
/track?url=suno.com/song/453a796e-a8e2-4d28-b24f-40f956cb5321
/track?url=suno.com/s/r4t4FIFyoU7GTnX8
/track?url=https://suno.com/song/453a796e-a8e2-4d28-b24f-40f956cb5321?sh=abc
```

### `GET /track/{id}`

Direct lookup by UUID:

```
/track/453a796e-a8e2-4d28-b24f-40f956cb5321
```

---

## Response

```json
{
  "status": "ok",
  "data": {
    "id":         "453a796e-a8e2-4d28-b24f-40f956cb5321",
    "mp3_url":    "https://cdn1.suno.ai/453a796e-....mp3",
    "cover_url":  "https://cdn2.suno.ai/image_453a796e-....jpeg",
    "cover_png":  "https://cdn2.suno.ai/image_453a796e-....png",
    "stream_url": "https://cdn1.suno.ai/453a796e-....mp3",
    "suno_url":   "https://suno.com/song/453a796e-...",
    "download": {
      "mp3":       "https://cdn1.suno.ai/453a796e-....mp3",
      "cover_jpg": "https://cdn2.suno.ai/image_453a796e-....jpeg",
      "cover_png": "https://cdn2.suno.ai/image_453a796e-....png"
    }
  }
}
```

Error:

```json
{
  "status": "error",
  "message": "No track ID found in URL",
  "data": null
}
```

---

## Deploy to Vercel (free, 2 min)

```bash
git clone https://github.com/yourname/sunoapi
cd sunoapi
npm i -g vercel
vercel
```

Or: push to GitHub → import on [vercel.com](https://vercel.com) → Deploy.

---

## Use from anything

**JavaScript**
```js
const res  = await fetch('https://your-api.vercel.app/track?url=suno.com/s/r4t4FIFyoU7GTnX8');
const json = await res.json();
const { mp3_url, cover_url } = json.data;

// play
const audio = new Audio(mp3_url);
audio.play();

// show cover
document.getElementById('cover').src = cover_url;
```

**Python**
```python
import requests

r    = requests.get('https://your-api.vercel.app/track?url=suno.com/s/r4t4FIFyoU7GTnX8')
data = r.json()['data']

print(data['mp3_url'])    # → https://cdn1.suno.ai/....mp3
print(data['cover_url'])  # → https://cdn2.suno.ai/image_....jpeg
```

**HTML — minimal player**
```html
<input id="url" placeholder="suno.com/s/...">
<button onclick="load()">Load</button>
<img id="cover" width="200">
<audio id="player" controls></audio>

<script>
async function load() {
  const url  = document.getElementById('url').value;
  const res  = await fetch(`https://your-api.vercel.app/track?url=${encodeURIComponent(url)}`);
  const data = (await res.json()).data;
  document.getElementById('cover').src      = data.cover_url;
  document.getElementById('player').src     = data.mp3_url;
}
</script>
```

**curl**
```bash
curl "https://your-api.vercel.app/track?url=suno.com/s/r4t4FIFyoU7GTnX8"
```

---

## Requirements (self-host)

- Python 3.9+
- `pip install fastapi httpx uvicorn`
- `uvicorn api.index:app --reload`

---

*Not affiliated with Suno Inc. · Unofficial · Personal use only*
