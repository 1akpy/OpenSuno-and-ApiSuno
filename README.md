<div align="center">

<br/>

# 🎵 OpenSuno

### Get MP3, cover and metadata from any Suno track — no API key, no sign-up

<br/>

[![Live](https://img.shields.io/badge/Live-opensuno.vercel.app-fb923c?style=flat-square)](https://opensuno.vercel.app)
[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-fb923c?style=flat-square)](LICENSE)

<br/>

Just drop a Suno link — get a direct MP3 URL, cover image and track metadata back in seconds.

<br/>

</div>

---

## Quick start

```bash
curl "https://opensuno.vercel.app/track?url=suno.com/s/FqENDOXo6l4yKQT0"
```

Any Suno link format works:

```
suno.com/s/{id}        — short link
suno.com/song/{uuid}   — full link
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

Fields with no value are omitted — you only get what's actually there.

---

## Examples

**Play in the browser**
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

**JavaScript**
```js
const { data } = await fetch('https://opensuno.vercel.app/track?url=suno.com/s/xxx')
  .then(r => r.json());

new Audio(data.mp3_url).play();
```

**Python**
```python
import requests

data = requests.get(
    'https://opensuno.vercel.app/track',
    params={'url': 'suno.com/s/FqENDOXo6l4yKQT0'}
).json()['data']

print(data['mp3_url'])
```

**curl**
```bash
curl "https://opensuno.vercel.app/track?url=suno.com/s/FqENDOXo6l4yKQT0"
```

---

## Run it yourself

```bash
git clone https://github.com/your-repo/opensuno
cd opensuno
pip install -r requirements.txt
uvicorn main:app --reload
```

---

<div align="center">

*Not affiliated with Suno Inc. · Unofficial · Personal use only*

</div>
