"""Artist profiles: who they are (Wikipedia, found through MusicBrainz), a photo (Deezer, else Wikipedia) and, with
a Last.fm key, Last.fm's bio for artists Wikipedia doesn't have."""
from __future__ import annotations

import hashlib
import html
import re
import time
from pathlib import Path
from urllib.parse import quote

from ..core import log as _log
from ..core.db import artist_key, get_db
from ..core.settings import get_settings
from . import services
from .services import MB_LIMIT, _http

log = _log.get("artists")

_WORDS = ("singer", "band", "musician", "rapper", "group", "songwriter", "producer", "dj", "composer", "duo",
          "idol", "vocalist", "artist", "orchestra", "unit")


def _same(a: str, b: str) -> bool:
    f = lambda s: re.sub(r"[^\w]+", "", (s or "").casefold())  # noqa: E731
    return f(a) == f(b) and bool(f(a))


def musicbrainz_artist(name: str) -> dict | None:
    MB_LIMIT.wait()
    r = _http.get("https://musicbrainz.org/ws/2/artist", params={"query": f'artist:"{name}"', "limit": 8,
                                                                 "fmt": "json"}, timeout=30)
    r.raise_for_status()
    best = None
    for a in r.json().get("artists") or []:
        names = [a.get("name", "")] + [x.get("name", "") for x in a.get("aliases") or []]
        if int(a.get("score", 0)) >= 85 and any(_same(n, name) for n in names):
            best = a
            break
    if best is None:
        return None
    MB_LIMIT.wait()
    r = _http.get(f"https://musicbrainz.org/ws/2/artist/{best['id']}", params={"inc": "url-rels", "fmt": "json"},
                  timeout=30)
    full = r.json() if r.ok else best
    ls = full.get("life-span") or {}
    years = ""
    if ls.get("begin"):
        years = ls["begin"][:4] + ("–" + ls["end"][:4] if ls.get("end") else "–")
    out = {"mbid": best["id"], "name": full.get("name") or name, "kind": full.get("type"),
           "country": full.get("country") or (full.get("area") or {}).get("name"), "years": years,
           "wikidata": None, "wikipedia": None}
    for rel in full.get("relations") or []:
        url = (rel.get("url") or {}).get("resource") or ""
        if rel.get("type") == "wikidata":
            out["wikidata"] = url.rstrip("/").rsplit("/", 1)[-1]
        elif rel.get("type") == "wikipedia":
            out["wikipedia"] = url
    return out


def wiki_title_from_wikidata(qid: str) -> tuple[str, str] | None:
    """(language, title) of the artist's Wikipedia page: English first, else Japanese, else any."""
    r = _http.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", timeout=30)
    if not r.ok:
        return None
    ent = (r.json().get("entities") or {}).get(qid) or {}
    links = ent.get("sitelinks") or {}
    for lang in ("en", "ja", "ko", "zh", "fr", "de", "es"):
        sl = links.get(f"{lang}wiki")
        if sl and sl.get("title"):
            return lang, sl["title"]
    for k, sl in links.items():
        if k.endswith("wiki") and len(k) <= 7 and sl.get("title"):
            return k[:-4], sl["title"]
    return None


def wiki_search(name: str) -> tuple[str, str] | None:
    r = _http.get("https://en.wikipedia.org/w/api.php", params={
        "action": "query", "list": "search", "srsearch": f'"{name}" musician OR band OR singer OR group',
        "srlimit": 5, "format": "json"}, timeout=30)
    if not r.ok:
        return None
    for res in (r.json().get("query") or {}).get("search") or []:
        t = res.get("title", "")
        snip = html.unescape(re.sub("<[^>]+>", "", res.get("snippet", ""))).lower()
        base = re.sub(r"\s*\(.*\)$", "", t)
        if _same(base, name) and any(w in (t.lower() + " " + snip) for w in _WORDS):
            return "en", t
    return None


def wiki_intro(lang: str, title: str) -> dict | None:
    r = _http.get(f"https://{lang}.wikipedia.org/w/api.php", params={
        "action": "query", "prop": "extracts|pageimages", "exintro": 1, "explaintext": 1, "redirects": 1,
        "piprop": "original", "titles": title, "format": "json"}, timeout=30)
    if not r.ok:
        return None
    pages = ((r.json().get("query") or {}).get("pages") or {}).values()
    for p in pages:
        text = (p.get("extract") or "").strip()
        if not text:
            continue
        t = p.get("title") or title
        return {"bio": text, "bio_source": f"Wikipedia ({lang})" if lang != "en" else "Wikipedia",
                "bio_url": f"https://{lang}.wikipedia.org/wiki/{quote(t.replace(' ', '_'))}",
                "image_url": (p.get("original") or {}).get("source")}
    return None


def lastfm_bio(name: str, key: str) -> dict | None:
    r = _http.get("https://ws.audioscrobbler.com/2.0/", params={
        "method": "artist.getinfo", "artist": name, "api_key": key, "format": "json", "autocorrect": 1}, timeout=30)
    if not r.ok:
        return None
    a = r.json().get("artist") or {}
    txt = ((a.get("bio") or {}).get("content") or "").strip()
    txt = re.sub(r"<a href=.*?</a>\.?", "", txt)
    txt = html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
    if len(txt) < 40:
        return None
    return {"bio": txt, "bio_source": "Last.fm", "bio_url": a.get("url")}


def deezer_image(name: str) -> str | None:
    r = _http.get("https://api.deezer.com/search/artist", params={"q": name, "limit": 5}, timeout=30)
    if not r.ok:
        return None
    for a in r.json().get("data") or []:
        pic = a.get("picture_xl") or a.get("picture_big")
        if _same(a.get("name", ""), name) and pic and "/artist//" not in pic:
            return pic
    return None


def fetch(name: str) -> dict:
    """Look an artist up and save what was found. Returns the saved fields."""
    s = get_settings()
    db = get_db()
    out: dict = {"status": "done", "fetched_at": time.time()}
    mb = None
    try:
        mb = musicbrainz_artist(name)
    except Exception as e:
        log.info("MusicBrainz artist lookup failed for %s: %s", name, e)
        out["status"] = "error"
    wiki = None
    try:
        if mb:
            out.update({k: mb[k] for k in ("mbid", "kind", "country", "years") if mb.get(k)})
            page = None
            if mb.get("wikidata"):
                page = wiki_title_from_wikidata(mb["wikidata"])
            elif mb.get("wikipedia"):
                m = re.match(r"https?://(\w+)\.wikipedia\.org/wiki/(.+)", mb["wikipedia"])
                if m:
                    from urllib.parse import unquote
                    page = (m.group(1), unquote(m.group(2)).replace("_", " "))
            if page:
                wiki = wiki_intro(*page)
        if wiki is None:
            page = wiki_search(name)
            if page:
                wiki = wiki_intro(*page)
    except Exception as e:
        log.info("Wikipedia lookup failed for %s: %s", name, e)
    if wiki:
        out.update(bio=wiki["bio"], bio_source=wiki["bio_source"], bio_url=wiki["bio_url"])
    key = (s.get("identify.lastfm_key") or "").strip()
    if not out.get("bio") and key:
        try:
            lf = lastfm_bio(name, key)
            if lf:
                out.update(lf)
        except Exception as e:
            log.info("Last.fm lookup failed for %s: %s", name, e)
    img_url = None
    try:
        img_url = deezer_image(name)
    except Exception as e:
        log.info("Deezer lookup failed for %s: %s", name, e)
    if not img_url and wiki:
        img_url = wiki.get("image_url")
    if img_url:
        dest = s.cache("artists") / f"{hashlib.md5(artist_key(name).encode()).hexdigest()[:16]}.jpg"
        if services.download(img_url, dest):
            out["image"] = str(dest)
    db.save_artist(name, **out)
    log.info("artist %s: %s%s", name, out.get("bio_source") or "no bio", ", photo" if out.get("image") else "")
    return out


def needs_fetch(name: str) -> bool:
    r = get_db().artist(name)
    if r is None or r["status"] == "pending":
        return True
    # try again a week after a failure (no internet then)
    return r["status"] == "error" and (time.time() - (r["fetched_at"] or 0)) > 7 * 86400
