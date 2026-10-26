#!/usr/bin/env python3
"""
Generate rotating collection (folder) artwork for Nuvio — or Stremio / Plex / Jellyfin — from TMDB.

    python generate.py                     # every folder, today's rotation
    python generate.py --only genres       # one category (or folders: genres/action,networks/hbo)
    python generate.py --check             # show what each folder's source matches on TMDB; no images
    python generate.py --demo              # no API key needed: synthetic images, for testing the look
    python generate.py --seed 2026-01-01   # reproduce a specific day's picks
    python generate.py --demo --design fan # force one design on every folder (quick comparisons)

Designs (set per folder with `design:` in collections.yaml, or a default under settings):
    backdrop   one film/show backdrop, full bleed, title over it
    spotlight  one poster on a blurred copy of itself
    fan        three posters fanned out, centre one in front
    mosaic     a tilted wall of posters

Needs TMDB_API_KEY in the environment (a v3 API key or a v4 "Read Access Token").
Folders built from your Simkl lists also need SIMKL_CLIENT_ID and SIMKL_REFRESH_TOKEN
(run simkl_login.py once).
Writes:
    art/<category>/<slug>/cover.jpg   1000x1500 poster-shaped folder
    art/<category>/<slug>/wide.jpg    1280x720 landscape folder
    manifest.json          every card's stable URL plus what it was built from
    index.html             a gallery to preview everything
"""
from __future__ import annotations

import argparse
import colorsys
import datetime as dt
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import yaml
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
ART = ROOT / "art"
FONT_TITLE = ROOT / "fonts" / "InterDisplay-Black.otf"
FONT_SUB = ROOT / "fonts" / "InterDisplay-SemiBold.otf"

API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p"

COVER = (1000, 1500)
WIDE = (1280, 720)
DESIGNS = ("backdrop", "spotlight", "fan", "mosaic", "logo", "topic", "basic")
POSTERS_NEEDED = {"backdrop": 0, "spotlight": 1, "fan": 3, "mosaic": 18, "logo": 18, "topic": 12, "basic": 0}
USES_TITLES = ("backdrop", "spotlight", "fan", "mosaic", "logo", "topic")  # built from movie/show art


# ============================================================================ TMDB


class TMDB:
    def __init__(self, key: str, language: str = "en-US"):
        self.key = key
        self.params = {"language": language}
        self._img: dict[str, Image.Image] = {}
        self._local = threading.local()
        if not key.startswith("eyJ"):  # v3 api key (v4 read-access tokens go in a header)
            self.params["api_key"] = key

    @property
    def s(self) -> requests.Session:
        """One session per thread (folders are built in parallel)."""
        if not hasattr(self._local, "s"):
            sess = requests.Session()
            if self.key.startswith("eyJ"):
                sess.headers["Authorization"] = f"Bearer {self.key}"
            self._local.s = sess
        return self._local.s

    def get(self, path: str, **params) -> dict:
        for attempt in range(5):
            r = self.s.get(f"{API}{path}", params={**self.params, **params}, timeout=20)
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()
        return {}

    def discover(self, media: str, pages: int = 1, **filters) -> list[dict]:
        filters.setdefault("sort_by", "popularity.desc")
        out = []
        for page in range(1, pages + 1):
            data = self.get(f"/discover/{media}", page=page, **filters)
            for item in data.get("results", []):
                item["media_type"] = media
                out.append(item)
            if page >= data.get("total_pages", 1):
                break
        return out

    def image(self, path: str, size: str = "original") -> Image.Image:
        key = f"{size}{path}"
        if key not in self._img:
            r = self.s.get(f"{IMG}/{size}{path}", timeout=30)
            r.raise_for_status()
            self._img[key] = Image.open(io.BytesIO(r.content)).convert("RGB")
        return self._img[key]


# TMDB uses different genre IDs for TV. Movie genres with a TV equivalent map here;
# ones with none (Horror, Thriller, Romance...) fall back to movies only.
MOVIE_TO_TV_GENRE = {
    28: 10759, 12: 10759,          # Action, Adventure -> Action & Adventure
    878: 10765, 14: 10765,         # Sci-Fi, Fantasy  -> Sci-Fi & Fantasy
    10752: 10768,                  # War -> War & Politics
    16: 16, 35: 35, 80: 80, 99: 99, 18: 18, 10751: 10751, 9648: 9648, 37: 37,
}
GENRE_ALIASES = {"sci-fi": "science fiction", "scifi": "science fiction", "musical": "music",
                 "action & adventure": "action", "sci-fi & fantasy": "science fiction"}

# TMDB has no network search, so networks are looked up here (IDs from TMDB).
NETWORKS = {
    "a&e": 129, "abc": 2, "adult swim": 80, "amc": 174, "bbc one": 4, "bravo": 74, "cartoon network": 56,
    "cbs": 16, "channel 4": 26, "comedy central": 47, "discovery": 64, "disney channel": 54, "fox": 19,
    "fx": 88, "hbo": 49, "hgtv": 210, "history": 65, "mtv": 33, "national geographic": 43, "nbc": 6,
    "nickelodeon": 13, "pbs": 14, "showtime": 67, "starz": 318, "syfy": 77, "tbs": 68, "the cw": 71,
    "tlc": 84, "tnt": 41, "usa network": 30, "bet": 24, "bbc two": 332, "bbc three": 3, "bbc four": 100,
    "itv": 9, "itv2": 149, "channel 5": 99, "e4": 136, "sky atlantic": 1063, "sky max": 5237, "dave": 388,
    "paramount network": 2076, "cinemax": 359, "freeform": 1267, "lifetime": 34, "hallmark channel": 384,
    "ifc": 124, "sundancetv": 270, "investigation discovery": 244, "food network": 143, "oxygen": 132,
    "travel channel": 209, "animal planet": 91, "e!": 76, "vh1": 158, "own": 827, "tv land": 397,
    "trutv": 364, "disney xd": 44, "disney junior": 281, "cbc": 23, "ctv": 110, "abc tv (au)": 18,
    "canal+": 285, "tokyo mx": 614, "tv tokyo": 98, "tvn": 866, "jtbc": 885, "we tv": 448, "vice tv": 3706,
    "bbc america": 493, "game show network": 252, "magnolia network": 4586, "cooking channel": 506,
    "nat geo wild": 443, "smithsonian channel": 658, "science channel": 95, "metv": 1618,
    "motortrend": 2470, "hallmark mystery": 2024,
}

SORTS = {"popularity": "popularity.desc", "rating": "vote_average.desc", "revenue": "revenue.desc"}


def norm(s: str) -> str:
    s = str(s).casefold().replace("+", " plus ").replace("&", " and ")
    return "".join(ch for ch in s if ch.isalnum())


def as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


class Resolver:
    """Turns names into TMDB IDs, once per run (shared by every folder)."""

    def __init__(self, tmdb: "TMDB", region: str):
        self.t, self.region = tmdb, region
        self.cache: dict = {}
        self.lock = threading.Lock()

    def _memo(self, key, fn):
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        val = fn()
        with self.lock:
            self.cache[key] = val
        return val

    def genre(self, name, media: str):
        """Genre ID for one media type, or None if that media has no such genre."""
        if isinstance(name, int) or str(name).isdigit():
            gid = int(name)
            return gid if media == "movie" else MOVIE_TO_TV_GENRE.get(gid, gid if gid > 10000 else None)
        table = self._memo(("genres", media), lambda: {
            norm(g["name"]): g["id"] for g in self.t.get(f"/genre/{media}/list").get("genres", [])})
        key = norm(GENRE_ALIASES.get(str(name).casefold(), name))
        if key in table:
            return table[key]
        if media == "tv":  # e.g. "Action" -> "Action & Adventure", "Science Fiction" -> "Sci-Fi & Fantasy"
            movie_id = self.genre(name, "movie")
            return MOVIE_TO_TV_GENRE.get(movie_id) if movie_id else None
        return None

    def keyword(self, name):
        if isinstance(name, int) or str(name).isdigit():
            return int(name), str(name)

        def look():
            res = self.t.get("/search/keyword", query=str(name)).get("results", [])
            exact = [r for r in res if norm(r["name"]) == norm(name)]
            words = str(name).casefold().split()
            loose = [r for r in res if all(w in r["name"].casefold() for w in words)]
            loose.sort(key=lambda r: len(r["name"]))
            hit = (exact or loose or [None])[0]
            return (hit["id"], hit["name"]) if hit else None
        return self._memo(("kw", norm(name)), look)

    def company(self, name):
        if isinstance(name, int) or str(name).isdigit():
            return int(name), str(name)

        def look():
            res = self.t.get("/search/company", query=str(name)).get("results", [])
            exact = [r for r in res if norm(r["name"]) == norm(name)]
            loose = [r for r in res if norm(name) in norm(r["name"])]
            # duplicates are common; the original company usually has the lowest ID and a logo
            pool = exact or loose
            pool.sort(key=lambda r: (r.get("logo_path") is None, r["id"]))
            return (pool[0]["id"], pool[0]["name"]) if pool else None
        return self._memo(("co", norm(name)), look)

    def network(self, name):
        if isinstance(name, int) or str(name).isdigit():
            return int(name), str(name)
        k = str(name).casefold()
        if k in NETWORKS:
            return NETWORKS[k], name
        hit = next((v for kk, v in NETWORKS.items() if norm(kk) == norm(name)), None)
        return (hit, name) if hit else None

    def provider(self, name, region: str):
        """Streaming service ID in a region, matched loosely ("HBO Max" = "Max", "Disney+" = "Disney Plus")."""
        if isinstance(name, int) or str(name).isdigit():
            return int(name), str(name)
        rec = self.provider_record(name, region)
        return (rec["provider_id"], rec["provider_name"]) if rec else None

    def provider_record(self, name, region: str):

        def table():
            out = {}
            for m in ("movie", "tv"):
                for p in self.t.get(f"/watch/providers/{m}", watch_region=region).get("results", []):
                    out.setdefault(p["provider_id"], p)
            return list(out.values())

        provs = self._memo(("prov", region), table)
        q = norm(name)
        ranked = sorted(provs, key=lambda p: p.get("display_priority", 999))
        for test in (lambda n: n == q, lambda n: n.startswith(q), lambda n: q in n):
            if isinstance(name, int) or str(name).isdigit():
                hit = next((p for p in ranked if p["provider_id"] == int(name)), None)
            else:
                hit = next((p for p in ranked if test(norm(p["provider_name"]))), None)
            if hit:
                return hit
        return None


def interleave(a: list[dict], b: list[dict]) -> list[dict]:
    """Merge two popularity-ordered lists so the rotation alternates movies and shows."""
    out = []
    for i in range(max(len(a), len(b))):
        out += [x[i] for x in (a, b) if i < len(x)]
    return out


# shorthand types are rewritten into the general `discover` form
SHORTHAND = {"genre": "genres", "company": "companies", "network": "networks",
             "keyword": "keywords", "provider": "providers", "country": "countries"}


def candidates(tmdb: TMDB, res: Resolver, src: dict, min_votes: int, notes: list) -> list[dict]:
    """Movies and/or shows for a folder's source, best first."""
    src = dict(src)
    t = src.pop("type", "discover")
    media = src.get("media", "both")
    want = ["movie", "tv"] if media == "both" else [media]
    min_votes = int(src.get("min_votes", min_votes))
    votes = {"movie": min_votes, "tv": max(1, min_votes // 3)}  # shows collect fewer votes

    def per_media(build) -> list[dict]:
        lists = [build(m) for m in want]
        return interleave(*lists) if len(lists) == 2 else lists[0]

    if t in SHORTHAND:  # {type: genre, name: Horror}  ->  {genres: [Horror]}
        key = SHORTHAND[t]
        vals = as_list(src.pop("name", None)) + as_list(src.pop("names", None)) + as_list(src.pop("id", None))
        src[key] = as_list(src.get(key)) + vals
        if t == "network":
            want = ["tv"]
        t = "discover"
    elif t == "decade":
        start = int(src.pop("start"))
        src["years"] = [start, start + 9]
        t = "discover"

    if t == "discover":
        region = src.get("region") or res.region

        def resolve(kind, names, fn):
            ids = []
            for n in as_list(names):
                # "HBO Max | Max" means: use the first of these that TMDB knows
                hit = next((h for h in (fn(a.strip()) for a in str(n).split(" | ")) if h), None)
                if not hit:
                    raise RuntimeError(f"no TMDB {kind} matches '{n}'")
                ids.append(hit[0])
                if str(hit[1]) != str(n):
                    notes.append(f"{kind} '{n}' → {hit[1]} ({hit[0]})")
            return ids

        kw_any = resolve("keyword", src.get("keywords"), res.keyword)
        kw_all = resolve("keyword", src.get("all_keywords"), res.keyword)
        cos = resolve("company", src.get("companies"), res.company)
        nets = resolve("network", src.get("networks"), res.network)
        provs = resolve("provider", src.get("providers"), lambda n: res.provider(n, region))
        if nets:
            want = ["tv"]
        sort = src.get("sort", "popularity")

        def build(m):
            f = {"vote_count.gte": votes[m]}
            for key, joiner in (("genres", ","), ("any_genres", "|")):
                names = as_list(src.get(key))
                if names:
                    ids = [res.genre(n, m) for n in names]
                    if key == "genres" and None in ids:
                        return []  # e.g. Horror has no TV genre: skip TV for this folder
                    ids = [i for i in ids if i]
                    if not ids:
                        return []
                    f["with_genres"] = joiner.join(str(i) for i in dict.fromkeys(ids))
            if kw_any:
                f["with_keywords"] = "|".join(map(str, kw_any))
            if kw_all:
                f["with_keywords"] = ",".join(map(str, kw_all))  # TMDB can't mix AND and OR
            if cos:
                f["with_companies"] = "|".join(map(str, cos))
            if nets:
                f["with_networks"] = "|".join(map(str, nets))
            if provs:
                f["with_watch_providers"] = "|".join(map(str, provs))
                f["watch_region"] = region
                f["with_watch_monetization_types"] = src.get("monetization", "flatrate|free|ads")
            if src.get("countries"):
                f["with_origin_country"] = "|".join(as_list(src["countries"]))
            if src.get("language"):
                f["with_original_language"] = src["language"]
            date = "primary_release_date" if m == "movie" else "first_air_date"
            if src.get("years"):
                y0, y1 = as_list(src["years"]) * 2 if len(as_list(src["years"])) == 1 else src["years"]
                f[f"{date}.gte"], f[f"{date}.lte"] = f"{y0}-01-01", f"{y1}-12-31"
            if src.get("recent_days"):
                today = dt.date.today()
                f[f"{date}.gte"] = (today - dt.timedelta(days=int(src["recent_days"]))).isoformat()
                f[f"{date}.lte"] = today.isoformat()
            s = SORTS.get(sort, sort)
            if s == "revenue.desc" and m == "tv":
                s = "popularity.desc"  # shows have no box office
            f["sort_by"] = s
            if src.get("filters"):
                f.update(src["filters"])  # raw TMDB discover parameters, for anything not covered above
            return tmdb.discover(m, pages=int(src.get("pages", 1)), **f)

        items = per_media(build)

    elif t == "trending":
        window = src.get("window", "week")
        kind = {"movie": "movie", "tv": "tv"}.get(media, "all")
        items = [i for i in tmdb.get(f"/trending/{kind}/{window}").get("results", [])
                 if i.get("media_type") in ("movie", "tv")]
    elif t == "person":
        role = src.get("role", "any")  # acting | directing | any

        def find_person(n):
            def look():
                r = tmdb.get("/search/person", query=str(n)).get("results", [])
                return (r[0]["id"], r[0]["name"]) if r else None
            hit = res._memo(("person", norm(n)), look)
            if not hit:
                raise RuntimeError(f"no TMDB person matches '{n}'")
            return hit[0]

        ids = as_list(src.get("id")) + [find_person(n) for n in as_list(src.get("name")) + as_list(src.get("names"))]
        seen, items = set(), []
        for pid in ids:
            credits = tmdb.get(f"/person/{pid}/combined_credits")
            pool = []
            if role in ("acting", "any"):
                pool += credits.get("cast", [])
            if role in ("directing", "any"):
                pool += [c for c in credits.get("crew", []) if c.get("job") in ("Director", "Creator")]
            for c in pool:
                k = (c.get("media_type"), c.get("id"))
                if k in seen or c.get("media_type") not in want or c.get("vote_count", 0) < min_votes // 3:
                    continue
                seen.add(k)
                items.append(c)
        items.sort(key=lambda c: c.get("vote_count", 0), reverse=True)
    elif t == "titles":
        items = []
        for ref in src["items"]:
            m, _, i = ref.partition("/")
            d = tmdb.get(f"/{m}/{i}")
            d["media_type"] = m
            items.append(d)
    else:
        raise ValueError(f"unknown source type: {t}")

    return [i for i in items if i.get("backdrop_path") or i.get("poster_path")]


# ============================================================================ Simkl
# Your own Simkl lists (watching, plan to watch, ...) as folder sources. Needs SIMKL_CLIENT_ID and
# SIMKL_REFRESH_TOKEN (run `python simkl_login.py` once to get the token).

SIMKL_API = "https://api.simkl.com"
SIMKL_APP = {"app-name": "my-nuvio-art", "app-version": "1.0"}
SIMKL_CACHE = Path(os.environ.get("SIMKL_CACHE", ROOT / ".simkl-cache.json"))
SIMKL_STATUSES = ("watching", "plantowatch", "hold", "completed", "dropped", "all")
SIMKL_BUCKETS = {"movie": ["movies"], "tv": ["shows"], "anime": ["anime"],
                 "both": ["movies", "shows"], "all": ["movies", "shows", "anime"]}


class Simkl:
    def __init__(self, client_id: str, refresh_token: str):
        self.client_id = client_id
        self.s = requests.Session()
        self.s.headers["User-Agent"] = f"{SIMKL_APP['app-name']}/{SIMKL_APP['app-version']}"
        self.q = {"client_id": client_id, **SIMKL_APP}
        r = self.s.post(f"{SIMKL_API}/oauth2/token", params=self.q, timeout=20, data={
            "grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id})
        if r.status_code != 200:
            raise RuntimeError(f"Simkl token refresh failed ({r.status_code}): {r.text[:200]} "
                               "— run simkl_login.py again and update SIMKL_REFRESH_TOKEN")
        self.s.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    def get(self, path: str) -> dict:
        r = self.s.get(f"{SIMKL_API}{path}", params=self.q, timeout=60)
        r.raise_for_status()
        return r.json() if r.content else {}


def load_simkl() -> dict:
    """All your Simkl list items, re-downloaded only when Simkl says something changed."""
    cid = os.environ.get("SIMKL_CLIENT_ID", "").strip()
    rt = os.environ.get("SIMKL_REFRESH_TOKEN", "").strip()
    if not cid or not rt:
        raise RuntimeError("SIMKL_CLIENT_ID / SIMKL_REFRESH_TOKEN are not set")
    api = Simkl(cid, rt)
    stamp = (api.get("/sync/activities") or {}).get("all")
    cache = {}
    if SIMKL_CACHE.exists():
        try:
            cache = json.loads(SIMKL_CACHE.read_text())
        except ValueError:
            cache = {}
    if stamp and cache.get("activities_all") == stamp:
        print("  simkl: lists unchanged since last run, using cache")
        return cache["items"]
    items = api.get("/sync/all-items")
    SIMKL_CACHE.write_text(json.dumps({"activities_all": stamp, "items": items}))
    print("  simkl: lists downloaded")
    return items


def simkl_candidates(tmdb: TMDB, data: dict, src: dict, limit: int) -> list[dict]:
    """Turn one Simkl list into TMDB titles, most recently active first."""
    status = src.get("status", "watching")
    if status not in SIMKL_STATUSES:
        raise ValueError(f"unknown simkl status '{status}' (choose from {', '.join(SIMKL_STATUSES)})")
    buckets = SIMKL_BUCKETS.get(src.get("media", "all"))
    if not buckets:
        raise ValueError("simkl media must be movie, tv, anime, both or all")

    rows = [(b, it) for b in buckets for it in data.get(b, []) or []
            if status == "all" or it.get("status") == status]
    # what you touched most recently leads: watched for watching/completed, added for the rest
    first, second = (("last_watched_at", "added_to_watchlist_at") if status in ("watching", "completed", "all")
                     else ("added_to_watchlist_at", "last_watched_at"))
    rows.sort(key=lambda r: r[1].get(first) or r[1].get(second) or "", reverse=True)

    out = []
    for bucket, it in rows[: limit * 2]:  # some won't match TMDB; look a little further
        if len(out) >= limit:
            break
        obj = it.get("movie") or it.get("show") or it.get("anime") or {}
        ids = obj.get("ids", {})
        m = "movie" if bucket == "movies" or it.get("anime_type") == "movie" else "tv"
        det = None
        try:
            if ids.get("tmdb"):
                det = tmdb.get(f"/{m}/{ids['tmdb']}")
            for ext, key in (("imdb_id", "imdb"), ("tvdb_id", "tvdb")):
                if det or not ids.get(key):
                    continue
                found = tmdb.get(f"/find/{ids[key]}", external_source=ext)
                hits = found.get(f"{m}_results") or found.get("tv_results") or found.get("movie_results")
                det = hits[0] if hits else None
        except requests.HTTPError:
            det = None
        if det:
            det["media_type"] = det.get("media_type", m)
            out.append(det)
        elif obj.get("poster"):  # no TMDB match (common for anime): use Simkl's own poster
            out.append({"id": ids.get("simkl"), "media_type": m, "title": obj.get("title"),
                        "poster_url": f"https://simkl.in/posters/{obj['poster']}_m.jpg"})
    return out


def poster_image(tmdb: TMDB, item: dict, size: str) -> Image.Image:
    if item.get("poster_path"):
        return tmdb.image(item["poster_path"], size)
    r = requests.get(item["poster_url"], timeout=30)  # plain request: never send the TMDB key elsewhere
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


# ============================================================================ helpers


def fill(img: Image.Image, size: tuple[int, int], cx: float = 0.5, cy: float = 0.4) -> Image.Image:
    """Scale to fill and crop."""
    return ImageOps.fit(img, size, Image.LANCZOS, centering=(cx, cy))


def accent_from(img: Image.Image) -> tuple[int, int, int]:
    """Pick the most vivid of the image's dominant colours, then brighten it so it reads on dark."""
    small = img.resize((64, 64)).quantize(colors=8).convert("RGB")
    best, best_score = (255, 255, 255), -1.0
    for count, rgb in small.getcolors(64 * 64) or []:
        h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
        score = s * (count ** 0.5) * (0.3 < l < 0.8)
        if score > best_score:
            best, best_score = rgb, score
    h, l, s = colorsys.rgb_to_hls(*(c / 255 for c in best))
    r, g, b = colorsys.hls_to_rgb(h, max(l, 0.55), min(1.0, max(s, 0.65)))
    return int(r * 255), int(g * 255), int(b * 255)


def hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def shade(img: Image.Image, mask: Image.Image, color=(8, 8, 12)) -> Image.Image:
    return Image.composite(Image.new("RGB", img.size, color), img, mask)


def vertical_mask(size, fn) -> Image.Image:
    """Build an L mask from fn(t) -> 0..255 where t runs 0 (top) to 1 (bottom)."""
    col = Image.new("L", (1, 256))
    for y in range(256):
        col.putpixel((0, y), int(max(0, min(255, fn(y / 255)))))
    return col.resize(size)


def horizontal_mask(size, fn) -> Image.Image:
    row = Image.new("L", (256, 1))
    for x in range(256):
        row.putpixel((x, 0), int(max(0, min(255, fn(x / 255)))))
    return row.resize(size)


def bottom_fade(t: float) -> float:
    a = 255 * max(0.0, (t - 0.35) / 0.65) ** 1.4
    return max(a, 70 * (1 - t / 0.25)) if t < 0.25 else a


def accent_wash(img: Image.Image, accent) -> Image.Image:
    """Faint glow of the accent colour along the bottom edge; ties the card together."""
    m = Image.linear_gradient("L").resize(img.size).point(lambda v: int(max(0, v - 170) * 0.55))
    return Image.composite(Image.new("RGB", img.size, accent), img, m)


def blurred_bg(img: Image.Image, size, darkness: float = 0.45, blur: int = 45) -> Image.Image:
    bg = fill(img, (size[0] // 4, size[1] // 4)).filter(ImageFilter.GaussianBlur(blur // 4))
    bg = ImageEnhance.Color(bg).enhance(1.3)
    bg = ImageEnhance.Brightness(bg).enhance(darkness)
    return bg.resize(size, Image.BICUBIC)


def card(poster: Image.Image, height: int, radius_frac: float = 0.03) -> Image.Image:
    """Poster scaled to height, 2:3, rounded corners, RGBA."""
    w = round(height * 2 / 3)
    p = fill(poster, (w, height), 0.5, 0.5).convert("RGBA")
    m = Image.new("L", (w, height), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, height - 1), radius=int(w * radius_frac * 1.5), fill=255)
    p.putalpha(m)
    return p


def paste_card(canvas: Image.Image, c: Image.Image, center, angle: float = 0, shadow: float = 0.7):
    """Drop a card (with soft shadow) onto the canvas, centred at `center`, rotated `angle` degrees."""
    if angle:
        c = c.rotate(angle, resample=Image.BICUBIC, expand=True)
    cx, cy = center
    x, y = int(cx - c.width / 2), int(cy - c.height / 2)
    if shadow:
        s = Image.new("L", canvas.size, 0)
        a = c.getchannel("A").point(lambda v: int(v * shadow))
        off = max(8, c.height // 40)
        s.paste(a, (x, y + off))
        s = s.filter(ImageFilter.GaussianBlur(max(10, c.height // 22)))
        canvas.paste(Image.new("RGB", canvas.size, (0, 0, 0)), (0, 0), s)
    canvas.paste(c, (x, y), c)


# ============================================================================ text


def fit_font(draw, text: str, path: Path, max_w: int, start: int, min_size: int):
    size = start
    while size > min_size:
        f = ImageFont.truetype(str(path), size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return ImageFont.truetype(str(path), min_size)


def wrap_title(draw, text: str, max_w: int, start: int, min_size: int):
    """One line if it fits at a decent size, otherwise split into two balanced lines."""
    f = fit_font(draw, text, FONT_TITLE, max_w, start, min_size)
    words = text.split()
    if f.size >= start * 0.6 or len(words) < 2:
        return [text], f
    best = min(
        range(1, len(words)),
        key=lambda i: abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))),
    )
    lines = [" ".join(words[:best]), " ".join(words[best:])]
    ref = ImageFont.truetype(str(FONT_TITLE), 100)
    longest = max(lines, key=lambda l: draw.textlength(l, font=ref))
    return lines, fit_font(draw, longest, FONT_TITLE, max_w, start, min_size)


def draw_title(img: Image.Image, title: str, subtitle: str | None, accent, box,
               valign: str = "bottom", align: str = "left", scale: float = 1.0) -> Image.Image:
    """Subtitle + big title + accent bar, laid out inside box=(x0, y0, x1, y1)."""
    if not title:  # covers without text
        return img
    w, h = img.size
    x0, y0, x1, y1 = box
    bw = x1 - x0
    draw = ImageDraw.Draw(img)

    start = int(min(w * 0.2, h * 0.19, bw * 0.26) * scale)
    lines, font = wrap_title(draw, title.upper(), bw, start=start, min_size=int(min(w, h) * 0.06))
    line_h = int(font.size * 0.98)
    block_h = line_h * len(lines)

    sub_font = ImageFont.truetype(str(FONT_SUB), max(18, int(min(w, h) * 0.036 * min(1.2, scale + 0.2))))
    sub_h = int(sub_font.size * 1.9) if subtitle else 0
    bar_h = max(6, int(min(w, h) * 0.009))
    gap = int(font.size * 0.28)
    total = sub_h + block_h + gap + bar_h

    top = y0 + (y1 - y0 - total) // 2 if valign == "center" else y1 - total

    def x_for(lw: float) -> int:
        return int(x0 + (bw - lw) / 2) if align == "center" else x0

    # shadow under the title for legibility on busy art
    sh = Image.new("L", img.size, 0)
    sd = ImageDraw.Draw(sh)
    y = top + sub_h
    for line in lines:
        sd.text((x_for(draw.textlength(line, font=font)), y + 4), line, font=font, fill=200)
        y += line_h
    sh = sh.filter(ImageFilter.GaussianBlur(max(6, font.size // 10)))
    img = Image.composite(Image.new("RGB", img.size, (0, 0, 0)), img, sh)
    draw = ImageDraw.Draw(img)

    if subtitle:
        st = subtitle.upper()
        tracking = int(sub_font.size * 0.22)
        sw = sum(draw.textlength(c, font=sub_font) for c in st) + tracking * (len(st) - 1)
        sx = x_for(sw)
        for c in st:
            draw.text((sx, top), c, font=sub_font, fill=accent)
            sx += draw.textlength(c, font=sub_font) + tracking

    y = top + sub_h
    for line in lines:
        draw.text((x_for(draw.textlength(line, font=font)), y), line, font=font, fill=(250, 250, 248))
        y += line_h

    bar_w = int(min(w, h) * 0.16)
    bx, by = x_for(bar_w), y + gap
    draw.rounded_rectangle((bx, by, bx + bar_w, by + bar_h), radius=bar_h // 2, fill=accent)
    return img


# ============================================================================ designs
# Each takes the art it needs, the output size and the text, and returns an RGB image.
# `style` is the text position: "bottom" (default) or "center".


def design_backdrop(bg, posters, size, title, subtitle, accent, style):
    """One backdrop, full bleed; the original look."""
    w, h = size
    base = fill(bg, size, 0.5 if w > h else 0.62)
    base = ImageOps.autocontrast(base, cutoff=1)
    if not title:  # no text to make room for: just a soft vignette
        return shade(base, Image.radial_gradient("L").resize(size).point(lambda v: int(v * 0.45)))
    if style == "center":
        base = shade(base, vertical_mask(size, lambda t: 120 + 60 * (1 - abs(t - 0.5) * 2)))
    else:
        base = shade(base, vertical_mask(size, bottom_fade))
    base = accent_wash(base, accent)
    pad = int(w * 0.075)
    return draw_title(base, title, subtitle, accent, (pad, 0, w - pad, int(h * 0.93)),
                      valign="center" if style == "center" else "bottom",
                      align="center" if style == "center" else "left")


def design_spotlight(bg, posters, size, title, subtitle, accent, style):
    """One poster on a blurred copy of itself."""
    w, h = size
    p = posters[0]
    base = blurred_bg(p, size, darkness=0.5)
    if not title:  # no text: the poster, centred
        ph = int(h * (0.74 if w < h else 0.84))
        paste_card(base, card(p, ph), (w // 2, h // 2))
        return base
    if w < h:  # cover: poster up top, title underneath
        ph = int(h * 0.56)
        c = card(p, ph)
        paste_card(base, c, (w // 2, int(h * 0.07) + ph // 2))
        base = shade(base, vertical_mask(size, lambda t: 255 * max(0, (t - 0.55) / 0.45) ** 1.2 * 0.85))
        base = accent_wash(base, accent)
        pad = int(w * 0.075)
        return draw_title(base, title, subtitle, accent, (pad, int(h * 0.66), w - pad, int(h * 0.95)),
                          valign="center", align="center", scale=0.8)
    # wide: poster on the right, title on the left
    ph = int(h * 0.80)
    c = card(p, ph)
    paste_card(base, c, (int(w * 0.76), h // 2), angle=-3)
    base = shade(base, horizontal_mask(size, lambda t: 210 * max(0, 1 - t / 0.62) ** 1.1))
    base = accent_wash(base, accent)
    pad = int(w * 0.07)
    return draw_title(base, title, subtitle, accent, (pad, 0, int(w * 0.56), h), valign="center", align="left")


def design_fan(bg, posters, size, title, subtitle, accent, style):
    """Three posters fanned out, the middle one in front."""
    w, h = size
    left, mid, right = (posters * 3)[:3]
    base = blurred_bg(mid, size, darkness=0.42)
    if not title:  # no text: the fan, centred
        ph = int(h * (0.6 if w < h else 0.78))
        cx, cy = w // 2, h // 2 + int(ph * 0.03)
        spread = int(w * (0.25 if w < h else 0.13))
        tilt = 9 if w < h else 10
        paste_card(base, card(left, int(ph * 0.84)), (cx - spread, cy + int(ph * 0.05)), angle=tilt)
        paste_card(base, card(right, int(ph * 0.84)), (cx + spread, cy + int(ph * 0.05)), angle=-tilt)
        paste_card(base, card(mid, ph), (cx, cy), shadow=0.85)
        return base
    if w < h:  # cover
        ph = int(h * 0.46)
        cy = int(h * 0.08) + ph // 2 + int(ph * 0.04)
        spread = int(w * 0.25)
        paste_card(base, card(left, int(ph * 0.84)), (w // 2 - spread, cy + int(ph * 0.05)), angle=9)
        paste_card(base, card(right, int(ph * 0.84)), (w // 2 + spread, cy + int(ph * 0.05)), angle=-9)
        paste_card(base, card(mid, ph), (w // 2, cy), shadow=0.85)
        base = shade(base, vertical_mask(size, lambda t: 255 * max(0, (t - 0.5) / 0.5) ** 1.2 * 0.85))
        base = accent_wash(base, accent)
        pad = int(w * 0.075)
        return draw_title(base, title, subtitle, accent, (pad, int(h * 0.64), w - pad, int(h * 0.95)),
                          valign="center", align="center", scale=0.8)
    # wide: fan on the right, title on the left
    ph = int(h * 0.74)
    cx, cy = int(w * 0.77), h // 2 + int(h * 0.02)
    spread = int(w * 0.10)
    paste_card(base, card(left, int(ph * 0.84)), (cx - spread, cy + int(ph * 0.04)), angle=10)
    paste_card(base, card(right, int(ph * 0.84)), (cx + spread, cy + int(ph * 0.04)), angle=-10)
    paste_card(base, card(mid, ph), (cx, cy), shadow=0.85)
    base = shade(base, horizontal_mask(size, lambda t: 215 * max(0, 1 - t / 0.55) ** 1.1))
    base = accent_wash(base, accent)
    pad = int(w * 0.07)
    return draw_title(base, title, subtitle, accent, (pad, 0, int(w * 0.5), h), valign="center", align="left")


def mosaic_wall(posters, size) -> Image.Image:
    """A tilted, staggered wall of posters filling `size`."""
    w, h = size
    angle = -12
    diag = int(math.hypot(w, h) * 1.08)
    tw = int(w / 3.1) if w < h else int(h / 2.3)       # tile width
    th = int(tw * 1.5)
    gap = max(6, tw // 22)
    wall = Image.new("RGB", (diag, diag), (10, 10, 14))
    cols = diag // (tw + gap) + 2
    rows = diag // (th + gap) + 2
    tiles = [card(p, th, radius_frac=0.02) for p in posters[: max(1, len(posters))]]
    i = 0
    for col in range(cols):
        y_off = -th // 2 if col % 2 else -th // 5          # stagger alternate columns
        for row in range(rows):
            t = tiles[i % len(tiles)]
            i += 1
            wall.paste(t, (col * (tw + gap) - tw // 3, y_off + row * (th + gap)), t)
    wall = wall.rotate(angle, resample=Image.BICUBIC)
    return wall.crop(((diag - w) // 2, (diag - h) // 2, (diag + w) // 2, (diag + h) // 2))


def design_mosaic(bg, posters, size, title, subtitle, accent, style):
    """A tilted, staggered wall of posters behind the title."""
    w, h = size
    base = ImageEnhance.Color(mosaic_wall(posters, size)).enhance(0.85)
    if not title:  # no text: just the wall, lightly dimmed at the edges
        return shade(base, Image.radial_gradient("L").resize(size).point(lambda v: int(30 + v * 0.4)))
    if style == "center":
        base = shade(base, vertical_mask(size, lambda t: 150 + 55 * (1 - abs(t - 0.5) * 2)))
    else:
        base = shade(base, vertical_mask(size, lambda t: 95 + 160 * min(1, max(0, (t - 0.25) / 0.5)) ** 1.2))
    base = accent_wash(base, accent)
    pad = int(w * 0.075)
    return draw_title(base, title, subtitle, accent, (pad, 0, w - pad, int(h * 0.93)),
                      valign="center" if style == "center" else "bottom",
                      align="center" if style == "center" else "left")


# ---------------------------------------------------------------------------- logo + topic designs


def tinted(accent, k: float) -> tuple[int, int, int]:
    return tuple(int(c * k) for c in accent)  # type: ignore[return-value]


def shift_hue(rgb, dh: float, l: float | None = None, s: float | None = None):
    h, ll, ss = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    r, g, b = colorsys.hls_to_rgb((h + dh) % 1, ll if l is None else l, ss if s is None else s)
    return int(r * 255), int(g * 255), int(b * 255)


def brand_bg(size, accent, seed: str, rich: bool = False) -> Image.Image:
    """Dark backdrop lit by soft blobs of the accent colour, with film grain. No movie art."""
    w, h = size
    rnd = random.Random(seed)
    sw, sh = max(1, w // 8), max(1, h // 8)  # draw small, blur, scale up: smooth and fast
    img = Image.new("RGB", (sw, sh), tinted(accent, 0.10))
    d = ImageDraw.Draw(img)
    blobs = [(accent, 0.5, 0.42 if w < h else 0.5, 0.62)]
    if rich:
        blobs = [(accent, rnd.uniform(0.15, 0.4), rnd.uniform(0.15, 0.35), 0.75),
                 (shift_hue(accent, rnd.choice([-0.09, 0.09]), l=0.45), rnd.uniform(0.6, 0.9), rnd.uniform(0.55, 0.8), 0.7),
                 (shift_hue(accent, 0.5, l=0.3, s=0.5), rnd.uniform(0.3, 0.7), 1.0, 0.55)]
    for color, cx, cy, r in blobs:
        rad = r * max(sw, sh) * 0.6
        d.ellipse((cx * sw - rad, cy * sh - rad, cx * sw + rad, cy * sh + rad), fill=color)
    img = img.filter(ImageFilter.GaussianBlur(max(sw, sh) // 5))
    img = img.resize((w, h), Image.BICUBIC)
    img = ImageEnhance.Brightness(img).enhance(0.62 if rich else 0.5)
    # vignette, then grain
    vig = Image.radial_gradient("L").resize((w, h)).point(lambda v: int(min(255, v * 1.1)))
    img = Image.composite(Image.new("RGB", size, (6, 6, 9)), img, vig.point(lambda v: int(v * 0.75)))
    grain = Image.effect_noise((w // 2, h // 2), 28).resize((w, h)).convert("RGB")
    return Image.blend(img, ImageChops.overlay(img, grain), 0.18)


def palette_accent(key: str) -> tuple[int, int, int]:
    """A pleasant, stable colour for a folder that has no image to take one from."""
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16) % 360 / 360
    r, g, b = colorsys.hls_to_rgb(h, 0.66, 0.75)
    return int(r * 255), int(g * 255), int(b * 255)


def logo_is_tile(logo: Image.Image) -> bool:
    """App-icon style square (no transparency) rather than a cut-out wordmark."""
    if logo.mode != "RGBA":
        return True
    return logo.getchannel("A").getextrema()[0] > 200


def prepare_logo(logo: Image.Image) -> Image.Image:
    """Trim transparent padding; turn dark or grey wordmarks white so they read on a dark card."""
    logo = logo.convert("RGBA")
    box = logo.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    if box:
        logo = logo.crop(box)
    small = logo.resize((64, max(1, round(64 * logo.height / max(1, logo.width)))))
    px = [(r, g, b) for r, g, b, a in small.getdata() if a > 128]
    if px:
        lum = sum(0.2126 * r + 0.7152 * g + 0.0722 * b for r, g, b in px) / len(px) / 255
        sat = sum(colorsys.rgb_to_hls(r / 255, g / 255, b / 255)[2] for r, g, b in px) / len(px)
        if lum < 0.42 or (sat < 0.12 and lum < 0.7):
            white = Image.new("RGBA", logo.size, (250, 250, 248, 0))
            white.putalpha(logo.getchannel("A"))
            logo = white
    return logo


def logo_accent(logo: Image.Image):
    """The logo's own brand colour, if it has a vivid one."""
    rgba = logo.convert("RGBA").resize((48, 48))
    px = [(r, g, b) for r, g, b, a in rgba.getdata() if a > 128]
    best, score = None, 0.0
    for r, g, b in px:
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s > 0.45 and 0.25 < l < 0.75 and s > score:
            best, score = (r, g, b), s
    return best


def small_caption(img: Image.Image, text: str | None, accent, y: int):
    """Tracked-out subtitle and accent bar, centred, starting at y."""
    w, h = img.size
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(str(FONT_SUB), max(18, int(min(w, h) * 0.036)))
    if text:
        st, track = text.upper(), int(f.size * 0.3)
        tw = sum(d.textlength(c, font=f) for c in st) + track * (len(st) - 1)
        x = (w - tw) / 2
        for c in st:
            d.text((x, y), c, font=f, fill=(225, 225, 225))
            x += d.textlength(c, font=f) + track
        y += int(f.size * 1.9)
    bw, bh = int(min(w, h) * 0.12), max(5, int(min(w, h) * 0.008))
    d.rounded_rectangle(((w - bw) // 2, y, (w + bw) // 2, y + bh), radius=bh // 2, fill=accent)


def design_logo(logo, posters, size, title, subtitle, accent, style):
    """The network / service / studio logo, big and centred over a dim wall of its titles. No text."""
    w, h = size
    cover = w < h
    if posters:  # a dim wall of the service's own titles behind the logo
        base = mosaic_wall(posters, size).filter(ImageFilter.GaussianBlur(max(2, w // 400)))
        base = ImageEnhance.Color(base).enhance(0.75)
        base = ImageEnhance.Brightness(base).enhance(0.42)
        spot = Image.radial_gradient("L").resize(size)  # darker in the middle, behind the logo
        base = shade(base, spot.point(lambda v: int(150 * (1 - v / 255) ** 1.5)))
        base = accent_wash(base, accent)
    else:
        base = brand_bg(size, accent, str(accent))
    if logo_is_tile(logo):  # square app icon: show it as a rounded tile
        side = int(min(w, h) * (0.46 if cover else 0.5))
        tile = fill(logo.convert("RGB"), (side, side), 0.5, 0.5).convert("RGBA")
        m = Image.new("L", (side, side), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, side - 1, side - 1), radius=int(side * 0.22), fill=255)
        tile.putalpha(m)
        paste_card(base, tile, (w // 2, h // 2), shadow=0.8)
    else:  # cut-out wordmark
        mark = prepare_logo(logo)
        bw, bh = (w * 0.72, h * 0.2) if cover else (w * 0.5, h * 0.34)
        k = min(bw / mark.width, bh / mark.height)
        mark = mark.resize((max(1, int(mark.width * k)), max(1, int(mark.height * k))), Image.LANCZOS)
        x, y = w // 2 - mark.width // 2, h // 2 - mark.height // 2
        glow = Image.new("L", size, 0)
        glow.paste(mark.getchannel("A").point(lambda v: int(v * 0.5)), (x, y))
        glow = glow.filter(ImageFilter.GaussianBlur(max(12, mark.height // 3)))
        base = Image.composite(Image.new("RGB", size, shift_hue(accent, 0, l=0.7)), base, glow)
        base.paste(mark, (x, y), mark)
    return base


def design_topic(bg, posters, size, title, subtitle, accent, style, dark=False):
    """The folder's name over a straight grid of its posters, tinted in the folder's colour."""
    w, h = size
    if posters:
        cols = 3 if w < h else 5
        gap = max(4, w // 160)
        tw = (w - gap * (cols + 1)) // cols
        th = int(tw * 1.5)
        rows = h // (th + gap) + 2
        grid = Image.new("RGB", size, (8, 8, 10))
        y0 = -th // 3
        for r in range(rows):
            for c in range(cols):
                p = posters[(r * cols + c) % len(posters)]
                grid.paste(fill(p, (tw, th), 0.5, 0.5), (gap + c * (tw + gap), y0 + r * (th + gap)))
        if dark:  # moody: black shadows, deep colour, dim highlights
            lo, hi, mid = (4, 4, 6), shift_hue(accent, 0, l=0.42, s=0.6), shift_hue(accent, 0, l=0.18, s=0.7)
        else:
            lo, hi, mid = shift_hue(accent, 0, l=0.08, s=0.6), shift_hue(accent, 0, l=0.82, s=0.55), accent
        base = ImageOps.colorize(ImageOps.autocontrast(grid.convert("L"), cutoff=2), lo, hi, mid=mid)
        base = Image.blend(base, grid, 0.08 if dark else 0.18)  # a hint of the real colours
        if not title:
            return base
        base = shade(base, vertical_mask(size, lambda t: 70 + 185 * max(0, (t - 0.35) / 0.65) ** 1.3))
        pad = int(w * 0.075)
        return draw_title(base, title, subtitle, accent, (pad, 0, w - pad, int(h * 0.93)),
                          valign="center" if style == "center" else "bottom",
                          align="center" if style == "center" else "left")
    base = brand_bg(size, accent, str(accent), rich=True)  # nothing to blur (e.g. an empty list)
    if not title:
        return base
    # a huge, faint first letter for some depth
    letter = title.strip()[:1].upper() or "·"
    big = ImageFont.truetype(str(FONT_TITLE), int(max(w, h) * 0.95))
    layer = Image.new("L", size, 0)
    ld = ImageDraw.Draw(layer)
    lw = ld.textlength(letter, font=big)
    ld.text((w - lw * 0.82, -max(w, h) * 0.12), letter, font=big, fill=34)
    base = Image.composite(Image.new("RGB", size, shift_hue(accent, 0, l=0.8)), base, layer)
    base = shade(base, vertical_mask(size, lambda t: 170 * max(0, (t - 0.45) / 0.55) ** 1.5))
    pad = int(w * 0.075)
    return draw_title(base, title, subtitle, accent, (pad, 0, w - pad, int(h * 0.93)),
                      valign="center" if style == "center" else "bottom",
                      align="center" if style == "center" else "left")


# where a streaming service has a TMDB network page, its wordmark is nicer than the app icon
PROVIDER_NETWORK = {
    "netflix": 213, "disneyplus": 2739, "hulu": 453, "amazonprimevideo": 1024, "primevideo": 1024,
    "appletv": 2552, "appletvplus": 2552, "hbomax": 3186, "max": 3186, "paramountplus": 4330,
    "peacock": 3353, "peacockpremium": 3353, "crunchyroll": 1112, "starz": 318,
}


def logo_kind(f: dict):
    """What a folder's logo would be, from its config alone: ('url'|'network'|'provider'|'company', value)."""
    if f.get("logo"):
        return "url", f["logo"]
    s = dict(f.get("source") or {})
    t = s.get("type", "discover")
    if t in ("network", "provider", "company"):
        names = as_list(s.get("name")) + as_list(s.get("names")) + as_list(s.get("id"))
        return (t, names[0]) if len(names) == 1 else None
    nets, provs, cos = (as_list(s.get(k)) for k in ("networks", "providers", "companies"))
    if len(nets) == 1 and not provs and not cos:
        return "network", nets[0]
    if len(provs) == 1 and not nets and not cos:
        return "provider", provs[0]
    if len(cos) == 1 and not nets and not provs:
        return "company", cos[0]
    return None


def fetch_logo_file(tmdb: "TMDB", path: str) -> Image.Image:
    """Download a TMDB logo keeping transparency. SVG logos: try TMDB's PNG copy, then cairosvg."""
    tries = [path] if not path.lower().endswith(".svg") else [path[:-4] + ".png", path]
    last = None
    for p in tries:
        r = tmdb.s.get(f"{IMG}/w500{p}" if p.lower().endswith(".png") else f"{IMG}/original{p}", timeout=30)
        if r.status_code != 200:
            last = RuntimeError(f"logo download failed ({r.status_code})")
            continue
        data = r.content
        if data.lstrip()[:1] == b"<":  # SVG
            try:
                import cairosvg
                data = cairosvg.svg2png(bytestring=data, output_width=1200)
            except Exception as e:
                last = RuntimeError(f"logo is SVG and couldn't be converted ({e})")
                continue
        return Image.open(io.BytesIO(data)).convert("RGBA")
    raise last or RuntimeError("no logo")


def entity_logo(tmdb: "TMDB", kind: str, eid: int) -> Image.Image:
    """Best logo for a TMDB network or company (PNG preferred, highest rated first)."""
    data = tmdb.get(f"/{kind}/{eid}/images")
    logos = sorted(data.get("logos", []), key=lambda l: (l.get("file_type") != ".png", -l.get("vote_average", 0)))
    paths = [l["file_path"] for l in logos]
    if not paths:
        info = tmdb.get(f"/{kind}/{eid}")
        paths = [info["logo_path"]] if info.get("logo_path") else []
    for p in paths[:3]:
        try:
            return fetch_logo_file(tmdb, p)
        except Exception:
            continue
    raise RuntimeError(f"TMDB has no usable logo for this {kind}")


def find_logo(f: dict, ctx) -> Image.Image:
    kind = logo_kind(f)
    if not kind:
        raise RuntimeError("no single network, service or studio to take a logo from")
    k, v = kind
    tmdb, res = ctx.tmdb, ctx.res
    if k == "url":
        r = requests.get(v, timeout=30)  # plain request: never send the TMDB key elsewhere
        r.raise_for_status()
        if r.content.lstrip()[:1] == b"<":
            import cairosvg
            return Image.open(io.BytesIO(cairosvg.svg2png(bytestring=r.content, output_width=1200))).convert("RGBA")
        return Image.open(io.BytesIO(r.content)).convert("RGBA")

    def first(fn):
        return next((h for h in (fn(a.strip()) for a in str(v).split(" | ")) if h), None)

    if k == "network":
        hit = first(res.network)
        if not hit:
            raise RuntimeError(f"unknown network '{v}'")
        return entity_logo(tmdb, "network", hit[0])
    if k == "company":
        hit = first(res.company)
        if not hit:
            raise RuntimeError(f"no TMDB company matches '{v}'")
        return entity_logo(tmdb, "company", hit[0])
    # provider: the matching TV network's wordmark if there is one, else the service's app icon
    region = (f.get("source") or {}).get("region") or res.region
    rec = first(lambda n: res.provider_record(n, region))
    if not rec:
        raise RuntimeError(f"no streaming service matches '{v}'")
    net = PROVIDER_NETWORK.get(norm(rec["provider_name"]))
    if net:
        try:
            return entity_logo(tmdb, "network", net)
        except Exception:
            pass
    if not rec.get("logo_path"):
        raise RuntimeError("TMDB has no logo for this service")
    return tmdb.image(rec["logo_path"], "original")


def demo_logo(seed: str, title: str) -> Image.Image:
    """Fake logo for --demo: a wordmark (white, dark or coloured) or an app-icon tile."""
    rnd = random.Random(seed)
    kind = rnd.choice(["white", "dark", "color", "tile"])
    if kind == "tile":
        img = Image.new("RGB", (400, 400), palette_accent(seed))
        d = ImageDraw.Draw(img)
        f = ImageFont.truetype(str(FONT_TITLE), 250)
        ch = title[:1].upper()
        d.text(((400 - d.textlength(ch, font=f)) / 2, 40), ch, font=f, fill=(255, 255, 255))
        return img
    f = ImageFont.truetype(str(FONT_TITLE), 160)
    word = title.upper()
    tw = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(word, font=f)) + 40
    img = Image.new("RGBA", (tw, 220), (0, 0, 0, 0))
    color = {"white": (255, 255, 255), "dark": (20, 20, 30), "color": palette_accent(seed + "c")}[kind]
    ImageDraw.Draw(img).text((20, 10), word, font=f, fill=color + (255,))
    return img


RENDERERS = {
    "backdrop": design_backdrop,
    "spotlight": design_spotlight,
    "fan": design_fan,
    "mosaic": design_mosaic,
    "logo": design_logo,
    "topic": design_topic,
}


# ============================================================================ demo images


def demo_background(seed: str, size=(1920, 1080)) -> Image.Image:
    """Abstract stand-in for a backdrop so the layout can be tested without an API key."""
    rnd = random.Random(seed)
    w, h = size
    hue = rnd.random()
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        r, g, b = colorsys.hls_to_rgb(hue, 0.12 + 0.25 * y / h, 0.55)
        d.line([(0, y), (w, y)], fill=(int(r * 255), int(g * 255), int(b * 255)))
    for _ in range(14):
        hh = (hue + rnd.uniform(-0.15, 0.15)) % 1
        r, g, b = colorsys.hls_to_rgb(hh, rnd.uniform(0.35, 0.7), rnd.uniform(0.5, 0.95))
        cx, cy, rad = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(w // 24, w // 4)
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(int(r * 255), int(g * 255), int(b * 255)))
    return img.filter(ImageFilter.GaussianBlur(w // 32))


DEMO_WORDS = ["NIGHTFALL", "ORBIT", "THE LAST RUN", "HOLLOW", "RED SKY", "ECHOES", "DRIFT",
              "IRONSIDE", "PALE BLUE", "VELOCITY", "HAVEN", "STATIC", "LOW TIDE", "EMBER", "MIRAGE",
              "THE PACT", "OUTLAND", "SIGNAL", "WILDER", "NORTHBOUND"]


DARK_WORDS = ("horror", "thriller", "crime", "war", "mystery", "noir", "gritty", "dark", "spooky", "creepy",
              "scary", "serial", "killer", "investigat", "scandal", "slow-burn", "slow burn", "tearjerk",
              "supernatural", "haunt", "apocalyp", "dystop", "survival", "western", "heist", "gangster")
# deep, moody hues: blood red, crimson, violet, dark teal, navy, ember
DARK_HUES = (0.0, 0.97, 0.78, 0.5, 0.62, 0.05)


def is_dark(f: dict) -> bool:
    """Dark topic? `mood: dark` / `mood: light` on a folder decides; otherwise its name does."""
    mood = str(f.get("mood", "")).casefold()
    if mood in ("dark", "light"):
        return mood == "dark"
    text = f"{f.get('path', '')} {f.get('title', '')}".casefold().replace("-", " ")
    words = [t for t in re.split(r"[^a-z]+", text) if t]
    for kw in DARK_WORDS:
        kw = kw.replace("-", " ")
        if " " in kw:
            if kw in " ".join(words):
                return True
        elif any(t == kw or (len(kw) > 4 and t.startswith(kw)) for t in words):
            return True
    return False


def dark_accent(key: str) -> tuple[int, int, int]:
    """A moody colour for a dark topic, still bright enough for the subtitle and bar."""
    h = DARK_HUES[int(hashlib.sha256(key.encode()).hexdigest(), 16) % len(DARK_HUES)]
    r, g, b = colorsys.hls_to_rgb(h, 0.5, 0.75)
    return int(r * 255), int(g * 255), int(b * 255)


def dark_background(seed: str, accent, size=(1920, 1080)) -> Image.Image:
    """Near-black with dim, deep glows of the folder's colour: `basic` for dark topics."""
    rnd = random.Random(seed)
    w, h = size
    hue = colorsys.rgb_to_hls(*(c / 255 for c in accent))[0]
    img = Image.new("RGB", (w, h), (5, 5, 7))
    d = ImageDraw.Draw(img)
    for i in range(9):
        hh = (hue + (rnd.uniform(-0.03, 0.03) if i % 3 else rnd.choice([-0.08, 0.08]))) % 1
        r, g, b = colorsys.hls_to_rgb(hh, rnd.uniform(0.1, 0.3), rnd.uniform(0.55, 0.85))
        cx, cy = rnd.randint(-w // 8, w + w // 8), rnd.randint(-h // 8, int(h * 0.75))
        rad = rnd.randint(w // 10, w // 4)
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(int(r * 255), int(g * 255), int(b * 255)))
    img = img.filter(ImageFilter.GaussianBlur(w // 18))
    grain = Image.effect_noise((w // 2, h // 2), 30).resize((w, h)).convert("RGB")
    return Image.blend(img, ImageChops.overlay(img, grain), 0.22)


def design_basic(bg, posters, size, title, subtitle, accent, style, dark=False):
    """Title over a colour background (`basic_background` or, for dark topics, `dark_background`)."""
    if not dark:
        return design_backdrop(bg, posters, size, title, subtitle, accent, style)
    w, h = size
    base = fill(bg, size, 0.5 if w > h else 0.62)  # no contrast stretch: keep it dark
    if not title:
        return base
    base = shade(base, vertical_mask(size, lambda t: 230 * max(0.0, (t - 0.45) / 0.55) ** 1.3))
    pad = int(w * 0.075)
    return draw_title(base, title, subtitle, accent, (pad, 0, w - pad, int(h * 0.93)),
                      valign="center" if style == "center" else "bottom",
                      align="center" if style == "center" else "left")


RENDERERS["basic"] = design_basic


def basic_background(seed: str, accent, size=(1920, 1080)) -> Image.Image:
    """Soft, vivid colour blobs around the folder's colour: the `basic` design's background."""
    rnd = random.Random(seed)
    w, h = size
    hue = colorsys.rgb_to_hls(*(c / 255 for c in accent))[0]
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        r, g, b = colorsys.hls_to_rgb(hue, 0.10 + 0.22 * y / h, 0.6)
        d.line([(0, y), (w, y)], fill=(int(r * 255), int(g * 255), int(b * 255)))
    for i in range(12):
        # mostly the folder's hue, with a few neighbouring hues for depth
        hh = (hue + (rnd.uniform(-0.06, 0.06) if i % 4 else rnd.choice([-0.16, 0.16]))) % 1
        r, g, b = colorsys.hls_to_rgb(hh, rnd.uniform(0.32, 0.62), rnd.uniform(0.45, 0.8))
        cx, cy, rad = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(w // 14, w // 4)
        d.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=(int(r * 255), int(g * 255), int(b * 255)))
    return img.filter(ImageFilter.GaussianBlur(w // 22))


def demo_poster(seed: str, dark: bool = False) -> Image.Image:
    """Stand-in 2:3 poster: abstract art plus a made-up title (used by --demo)."""
    rnd = random.Random(seed)
    w, h = 500, 750
    img = dark_background(seed, dark_accent(seed), (w, h)) if dark else demo_background(seed, (w, h))
    img = shade(img, vertical_mask((w, h), lambda t: 230 * max(0, (t - 0.6) / 0.4)))
    d = ImageDraw.Draw(img)
    word = rnd.choice(DEMO_WORDS)
    f = fit_font(d, word, FONT_TITLE, int(w * 0.84), 90, 30)
    d.text(((w - d.textlength(word, font=f)) / 2, h * 0.8), word, font=f, fill=(245, 240, 230))
    sf = ImageFont.truetype(str(FONT_SUB), 18)
    tag = "IN CINEMAS" if rnd.random() < 0.5 else "NOW STREAMING"
    d.text(((w - d.textlength(tag, font=sf)) / 2, h * 0.8 + f.size + 12), tag, font=sf, fill=(200, 200, 200))
    return img


# ============================================================================ picking


def raw_base() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")  # set automatically inside GitHub Actions
    branch = os.environ.get("ART_BRANCH", "art")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/{branch}"
    return f"https://raw.githubusercontent.com/YOUR-USERNAME/YOUR-REPO/{branch}"


def day_index(slug: str, seed: str, n: int) -> int:
    """Same pick all day, different pick tomorrow; never repeats yesterday's if avoidable."""
    def idx(s):
        return int(hashlib.sha256(f"{slug}:{s}".encode()).hexdigest(), 16) % n

    i = idx(seed)
    try:
        yesterday = (dt.date.fromisoformat(seed) - dt.timedelta(days=1)).isoformat()
        if n > 1 and i == idx(yesterday):
            i = (i + 1) % n
    except ValueError:
        pass
    return i


def pick_many(items: list[dict], slug: str, seed: str, k: int) -> list[dict]:
    """k items for today: the day's lead pick first, then a seeded shuffle of the rest."""
    if not items:
        return []
    lead = day_index(slug, seed, len(items))
    rest = items[:lead] + items[lead + 1 :]
    random.Random(f"{slug}:{seed}").shuffle(rest)
    return ([items[lead]] + rest)[:k]


# ============================================================================ config


NAME_FROM_TITLE = ("network", "provider", "company", "person", "keyword", "genre")
FOLDER_KEYS = ("subtitle", "design", "designs", "style", "accent", "pool", "rotate", "mood", "show_title")


def load_folders(cfg: dict) -> list[dict]:
    """Flatten collections.yaml into one list of folders, each with a `path` like genres/action."""
    folders = []
    for c in cfg.get("collections") or []:  # flat list (no category)
        f = dict(c)
        f.update(path=c["slug"], category="")
        folders.append(f)
    for cat in cfg.get("categories") or []:
        name = cat["name"]
        for item in cat.get("folders") or []:
            f = {k: cat[k] for k in FOLDER_KEYS if k in cat}  # category defaults...
            f.update(item)                                    # ...overridden per folder
            f["source"] = {**(cat.get("source") or {}), **(item.get("source") or {})}
            f["category"] = cat.get("label", name.replace("-", " ").title())
            f["path"] = f"{name}/{item['slug']}"
            folders.append(f)
    seen = set()
    for f in folders:
        if f["path"] in seen:
            raise SystemExit(f"duplicate folder: {f['path']}")
        seen.add(f["path"])
        s = f["source"]
        # {type: network} with no name uses the folder's title, so `- {slug: hbo, title: HBO}` is enough
        if s.get("type") in NAME_FROM_TITLE and not any(k in s for k in ("name", "names", "id")):
            s["name"] = f["title"]
    return folders


def matches(f: dict, only: set[str]) -> bool:
    return bool({f["path"], f["slug"], f["path"].split("/")[0]} & only)


# ============================================================================ building


class Ctx:
    def __init__(self, args, settings, tmdb):
        self.args, self.settings, self.tmdb = args, settings, tmdb
        self.res = Resolver(tmdb, settings.get("region", "US")) if tmdb else None
        self._simkl = None
        self._simkl_lock = threading.Lock()

    def simkl(self) -> dict:
        with self._simkl_lock:  # fetched once, shared by every Simkl folder
            if self._simkl is None:
                self._simkl = load_simkl()
            return self._simkl

    def items_for(self, f: dict, need: int, notes: list) -> list[dict]:
        src = f["source"]
        if src.get("type") == "simkl":
            return simkl_candidates(self.tmdb, self.simkl(), src, max(int(f.get("pool", 20)), need, 1))
        return candidates(self.tmdb, self.res, src, int(self.settings.get("min_votes", 300)), notes)


def build_folder(f: dict, ctx: Ctx, base: str) -> tuple[dict, str]:
    """Render one folder in every design. Returns its manifest entry and a log line.

    The folder's main design is also saved as art/<path>/cover.jpg + wide.jpg (the links you put
    in Nuvio); every design, main included, is saved as art/<path>/<design>/cover.jpg + wide.jpg.
    """
    args, path, src = ctx.args, f["path"], f["source"]
    main = args.design or f.get("design", ctx.settings.get("design", "backdrop"))
    wanted = as_list(f.get("designs") or ctx.settings.get("designs") or list(DESIGNS))
    designs = [main] + [d for d in wanted if d != main]
    # the logo design only applies to folders about one network, streaming service or studio
    designs = [d for d in designs if d != "logo" or d == main or logo_kind(f)]
    entry = {"path": path, "slug": f["slug"], "title": f["title"], "category": f["category"],
             "design": main, "cover_url": f"{base}/art/{path}/cover.jpg",
             "wide_url": f"{base}/art/{path}/wide.jpg", "designs": {}}
    notes: list[str] = []
    out = ART / path
    try:
        bad = [d for d in designs if d not in RENDERERS]
        if bad:
            raise ValueError(f"unknown design '{bad[0]}' (choose from {', '.join(DESIGNS)})")
        uses_titles = any(d in USES_TITLES for d in designs)
        items = [] if args.demo or not uses_titles else ctx.items_for(f, max(POSTERS_NEEDED[d] for d in designs), notes)
        pool = int(f.get("pool", 20))
        # Simkl folders default to "most recent first"; everything else rotates nightly
        rotate = f.get("rotate", src.get("type") != "simkl")
        accent = hex_rgb(f["accent"]) if f.get("accent") else None
        dark = is_dark(f)
        # text on the cover is off unless `show_title: true` (settings, category or folder)
        show = f.get("show_title", ctx.settings.get("show_title", False))
        text_title, text_sub = (f["title"], f.get("subtitle")) if show else (None, None)

        for d in designs:
            try:
                need = POSTERS_NEEDED[d]
                bg, posters, picked, render_as = None, [], [], d
                if d == "basic":  # no movie art: colour blobs in the folder's colour, never changes
                    basic_acc = hex_rgb(f["accent"]) if f.get("accent") else (
                        dark_accent(path) if dark else palette_accent(path))
                    bg = (dark_background if dark else basic_background)(path, basic_acc)
                    built_from = ["basic"]
                elif d in ("logo", "topic"):  # posters are the background; works without them too
                    if d == "logo":
                        bg = demo_logo(path, f["title"]) if args.demo else find_logo(f, ctx)
                    if args.demo:
                        posters = [demo_poster(f"{path}:{args.seed}:{i}") for i in range(12)]
                    else:
                        usable = [i for i in items if i.get("poster_path") or i.get("poster_url")][: max(pool, need)]
                        if usable:
                            picked = pick_many(usable, path, args.seed, need) if rotate else usable[:need]
                            posters = [poster_image(ctx.tmdb, p, "w342") for p in picked]
                    built_from = [p.get("title") or p.get("name") or "?" for p in picked] or [d]
                elif args.demo:
                    if d == "backdrop":
                        bg = demo_background(f"{path}:{args.seed}")
                    else:
                        posters = [demo_poster(f"{path}:{args.seed}:{i}") for i in range(min(need, 12))]
                    built_from = ["demo images"]
                else:
                    if d == "backdrop":
                        usable = [i for i in items if i.get("backdrop_path")][:pool]
                        if usable:
                            chosen = usable[day_index(path, args.seed, len(usable))] if rotate else usable[0]
                            bg = ctx.tmdb.image(chosen["backdrop_path"], "w1280")
                            picked = [chosen]
                    else:
                        usable = [i for i in items if i.get("poster_path") or i.get("poster_url")][: max(pool, need)]
                        if usable:
                            picked = pick_many(usable, path, args.seed, need) if rotate else usable[:need]
                            size = "w342" if d == "mosaic" else "w780"
                            posters = [poster_image(ctx.tmdb, p, size) for p in picked]
                    if picked:
                        built_from = [p.get("title") or p.get("name") or "?" for p in picked]
                    elif src.get("type") == "simkl" and not items:
                        # an empty list (nothing on hold, say) still gets a cover, just an abstract one
                        bg, render_as, built_from = demo_background(f"{path}:empty"), "backdrop", ["(list is empty)"]
                    elif d == "backdrop":
                        raise RuntimeError("no titles with backdrops (try a poster design)")
                    else:
                        raise RuntimeError("source returned no titles with usable images")

                if d == "logo":  # the brand's own colour where it has one
                    acc = (hex_rgb(f["accent"]) if f.get("accent") else None) or logo_accent(bg) \
                        or accent or palette_accent(path)
                elif d == "topic":  # dark topics always get a moody colour, not one from the posters
                    acc = (hex_rgb(f["accent"]) if f.get("accent") else dark_accent(path)) if dark \
                        else (accent or palette_accent(path))
                elif d == "basic":
                    acc = basic_acc
                else:
                    if accent is None:  # pick once, so every design of a folder shares its colour
                        accent = accent_from(bg or posters[0])
                    acc = accent
                (out / d).mkdir(parents=True, exist_ok=True)
                for name, size in (("cover", COVER), ("wide", WIDE)):
                    extra = {"dark": dark} if render_as in ("topic", "basic") else {}
                    img = RENDERERS[render_as](bg, posters, size, text_title, text_sub, acc,
                                               f.get("style", "bottom"), **extra)
                    img.save(out / d / f"{name}.jpg", "JPEG", quality=86, optimize=True, progressive=True)
                    if d == main:
                        shutil.copyfile(out / d / f"{name}.jpg", out / f"{name}.jpg")
                entry["designs"][d] = {"cover_url": f"{base}/art/{path}/{d}/cover.jpg",
                                       "wide_url": f"{base}/art/{path}/{d}/wide.jpg"}
                if d == main:
                    entry["built_from"] = built_from
                    if picked:
                        entry["tmdb"] = [f"{p.get('media_type', 'movie')}/{p['id']}" for p in picked]
            except Exception as e:
                if d == main:
                    raise
                entry.setdefault("design_errors", {})[d] = str(e)

        # clear out designs this folder no longer uses (e.g. one removed from `designs:`)
        for sub in out.iterdir():
            if sub.is_dir() and sub.name not in designs:
                shutil.rmtree(sub, ignore_errors=True)

        # your Simkl lists stay out of the public manifest/gallery (the images still show posters)
        if src.get("type") == "simkl" and not src.get("show_titles", False) and not args.demo:
            entry["built_from"] = [f"simkl {src.get('status', 'watching')}"]
            entry.pop("tmdb", None)
        others = [d for d in entry["designs"] if d != main]
        line = (f"✓ {path:<44} {main:<10} {', '.join(entry['built_from'][:3])}"
                + (f"   (+{', '.join(others)})" if others else ""))
        for d, err in entry.get("design_errors", {}).items():
            line += f"\n    · {d} skipped: {err}"
    except Exception as e:  # keep going; yesterday's files stay in place
        entry["error"] = str(e)
        line = f"✗ {path:<44} {e}"
    if notes:
        entry["matched"] = notes
        line += "".join(f"\n    · {n}" for n in notes)
    return entry, line


def check_folder(f: dict, ctx: Ctx) -> str:
    """--check: show what a folder's source matches, without downloading any images."""
    notes: list[str] = []
    try:
        items = ctx.items_for(f, 3, notes)
        top = ", ".join((i.get("title") or i.get("name") or "?") for i in items[:4])
        line = f"{'✓' if items else '!'} {f['path']:<44} {len(items):>3} titles  {top}"
    except Exception as e:
        line = f"✗ {f['path']:<44} {e}"
    return line + "".join(f"\n    · {n}" for n in notes)


# ============================================================================ gallery


def write_gallery(manifest: dict) -> None:
    groups: dict[str, list] = {}
    for c in manifest["collections"]:
        groups.setdefault(c.get("category") or "Folders", []).append(c)

    def card(c):
        return f"""<figure><img src="art/{c['path']}/cover.jpg" alt="{c['title']}" loading="lazy">
<img class="wide" src="art/{c['path']}/wide.jpg" alt="" loading="lazy">
<figcaption><b>{c['title']}</b><span>{c.get('design', '')} · {', '.join(c.get('built_from', [])) or c.get('error', '')}</span>
<code>{c['cover_url']}</code><code>{c['wide_url']}</code>{designs(c)}</figcaption></figure>"""

    def designs(c):
        links = "<br>".join(f'{d}: <a href="art/{c['path']}/{d}/cover.jpg">cover</a> '
                            f'<a href="art/{c['path']}/{d}/wide.jpg">wide</a>' for d in c.get("designs", {}))
        return f"<span class=d>{links}</span>" if links else ""

    body = "\n".join(f"<h2>{g} <small>{len(cs)}</small></h2><div class=\"g\">{''.join(card(c) for c in cs)}</div>"
                     for g, cs in groups.items())
    (ROOT / "index.html").write_text(
        f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Collection Art</title>
<style>
body{{margin:0;background:#0d0d10;color:#eee;font:14px/1.4 system-ui,sans-serif;padding:24px}}
h1{{font-weight:800;letter-spacing:-.02em}} h2{{margin:40px 0 16px}} small{{color:#777;font-weight:400}} p{{color:#999}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:20px}}
figure{{margin:0;background:#16161b;border-radius:12px;overflow:hidden}}
img{{width:100%;display:block}} .wide{{border-top:1px solid #222}}
figcaption{{padding:12px;display:grid;gap:4px}} span{{color:#888}}
code{{font-size:11px;color:#7ab;word-break:break-all;user-select:all}} .d a{{color:#7ab;margin-right:6px}}
</style>
<h1>Collection Art</h1><p>Generated {manifest['generated']} · rotation seed {manifest['seed']}</p>
{body}
<p>Images from <a style="color:#7ab" href="https://www.themoviedb.org">TMDB</a>.
This product uses the TMDB API but is not endorsed or certified by TMDB.</p>""",
        encoding="utf-8",
    )


# ============================================================================ main


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "collections.yaml"))
    ap.add_argument("--only", help="comma-separated folders, slugs or whole categories (e.g. genres,moods-and-vibes)")
    ap.add_argument("--seed", default=dt.date.today().isoformat(), help="rotation seed (default: today)")
    ap.add_argument("--demo", action="store_true", help="synthetic images, no API key")
    ap.add_argument("--design", choices=DESIGNS, help="override every folder's design")
    ap.add_argument("--check", action="store_true", help="show what each folder's source matches; no images")
    ap.add_argument("--workers", type=int, help="folders built at once (default 6)")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    settings = cfg.get("settings", {})
    folders = load_folders(cfg)
    only = set(args.only.split(",")) if args.only else None
    todo = [f for f in folders if not only or matches(f, only)]
    workers = args.workers or int(settings.get("workers", 6))

    tmdb = None
    if not args.demo:
        key = os.environ.get("TMDB_API_KEY", "").strip()
        if not key:
            print("TMDB_API_KEY is not set (or run with --demo).", file=sys.stderr)
            return 2
        tmdb = TMDB(key, settings.get("language", "en-US"))
    elif args.check:
        print("--check needs a TMDB key (it looks names up).", file=sys.stderr)
        return 2
    ctx = Ctx(args, settings, tmdb)

    if args.check:
        with ThreadPoolExecutor(workers) as pool:
            for line in pool.map(lambda f: check_folder(f, ctx), todo):
                print(line)
        return 0

    # keep entries for folders we're not rebuilding this run, so the gallery stays complete
    old = {}
    if (ROOT / "manifest.json").exists():
        try:
            old = {c.get("path", c["slug"]): c
                   for c in json.loads((ROOT / "manifest.json").read_text())["collections"]}
        except (ValueError, KeyError):
            pass

    base = raw_base()
    print(f"Building {len(todo)} of {len(folders)} folders ({workers} at a time)...")
    with ThreadPoolExecutor(workers) as pool:
        results = dict(zip((f["path"] for f in todo), pool.map(lambda f: build_folder(f, ctx, base), todo)))

    manifest = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "seed": args.seed, "collections": []}
    built = failed = 0
    for f in folders:
        if f["path"] in results:
            entry, line = results[f["path"]]
            print(line, file=sys.stderr if "error" in entry else sys.stdout)
            failed += "error" in entry
            built += "error" not in entry
            manifest["collections"].append(entry)
        elif f["path"] in old:
            manifest["collections"].append(old[f["path"]])

    if not only and ART.exists():  # full run: clear out folders no longer in collections.yaml
        keep = {f["path"] for f in folders}
        for cat in [d for d in ART.iterdir() if d.is_dir()]:
            for folder in [d for d in cat.iterdir() if d.is_dir()]:
                if f"{cat.name}/{folder.name}" not in keep:
                    shutil.rmtree(folder, ignore_errors=True)
            if not any(cat.iterdir()):
                cat.rmdir()

    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    write_gallery(manifest)
    print(f"\n{built} built, {failed} failed. Gallery: index.html")
    return 1 if failed and not built else 0


if __name__ == "__main__":
    sys.exit(main())
