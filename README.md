<div align="center">

<br/>

# 🎵 SunoAPI

### Unofficial Suno track resolver — free & open source

<br/>

[![Live](https://img.shields.io/badge/Live-opensuno.vercel.app-fb923c?style=flat-square)](https://opensuno.vercel.app)
[![Deploy](https://img.shields.io/badge/Deploy-Vercel-000?style=flat-square&logo=vercel)](https://vercel.com/new/clone?repository-url=https://github.com/1akpy/sunoapi)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-fb923c?style=flat-square)](LICENSE)

<br/>

Pass any Suno link → get MP3, cover image and metadata. No API key. No auth. Use from any language.

<br/>

</div>

---

## Endpoints

```
GET /track?url=suno.com/s/{id}              short link
GET /track?url=suno.com/song/{uuid}         full link
GET /track/{uuid}                           direct UUID
```

---

## Response

```json
{
  "status": "ok",
  "data": {
    "id":        "453a796e-a8e2-4d28-b24f-40f956cb5321",
    "suno_url":  "https://suno.com/song/453a796e-...",
    "mp3_url":   "https://cdn1.suno.ai/453a796e-....mp3",
    "cover_url": "https://cdn2.suno.ai/image_453a796e-....jpeg",
    "cover_png": "https://cdn2.suno.ai/image_453a796e-....png",
    "download": {
      "mp3":       "https://cdn1.suno.ai/453a796e-....mp3",
      "cover_jpg": "https://cdn2.suno.ai/image_453a796e-....jpeg",
      "cover_png": "https://cdn2.suno.ai/image_453a796e-....png"
    },
    "title":    "Track title",
    "artist":   "Artist name",
    "tags":     "pop electronic",
    "duration": 180
  }
}
```

> Null fields are omitted — only available data is returned.

---

## Usage

**JavaScript**
```js
const res  = await fetch('https://opensuno.vercel.app/track?url=suno.com/s/xxx');
const { data } = await res.json();

new Audio(data.mp3_url).play();
document.getElementById('cover').src = data.cover_url;
```

**Python**
```python
import requests

data = requests.get(
    'https://opensuno.vercel.app/track',
    params={'url': 'suno.com/s/FqENDOXo6l4yKQT0'}
).json()['data']

print(data['mp3_url'])
print(data['cover_url'])
```

**curl**
```bash
curl "https://opensuno.vercel.app/track?url=suno.com/s/FqENDOXo6l4yKQT0"
```

**HTML**
```html
<img id="cover">
<audio id="player" controls></audio>

<script>
fetch('https://opensuno.vercel.app/track?url=suno.com/s/xxx')
  .then(r => r.json())
  .then(({ data }) => {
    document.getElementById('cover').src  = data.cover_url;
    document.getElementById('player').src = data.mp3_url;
  });
</script>
```

---

## Deploy your own

**Vercel (recommended, free)**

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new/clone?repository-url=https://github.com/1akpy/sunoapi)

or manually:

```bash
git clone https://github.com/1akpy/sunoapi
cd sunoapi
vercel
```

**Local**

```bash
pip install fastapi httpx uvicorn
uvicorn api.index:app --reload
# → http://localhost:8000
```

---

<div align="center">

*Not affiliated with Suno Inc. · Unofficial · Personal use only*

</div>
