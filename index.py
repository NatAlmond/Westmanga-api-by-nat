"""
Westmanga API by Nat — v1.0.1
Sumber: https://v1.westmanga.my

Hulu:
  GET /api/visit-token                 → X-WM-Visit
  GET /api/contents?page&per_page&type=Comic&project&q&orderBy
  GET /api/comic/{slug}
  GET /api/v/{chapterSlug}             → data.images[]

Auth cadangan: data.mantweh.online + HMAC (x-wm-accses-key typo resmi hulu)
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from mangum import Mangum

def _uniq(seq: List[str]) -> List[str]:
    seen = set()
    out = []
    for x in seq:
        x = (x or "").rstrip("/")
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out

HOSTS = _uniq([
    os.environ.get("WESTMANGA_BASE", "https://v1.westmanga.my"),
    "https://westmanga.co",
    "https://data.mantweh.online",
])

TIMEOUT = float(os.environ.get("WESTMANGA_TIMEOUT", "30"))
ACCESS_KEY = os.environ.get("WESTMANGA_ACCESS_KEY", "WM_WEB_FRONT_END")
SECRET_KEY = os.environ.get("WESTMANGA_SECRET_KEY", "xxxoidj")
PER_PAGE = int(os.environ.get("WESTMANGA_PER_PAGE", "24"))

app = FastAPI(title="Westmanga API by Nat", version="1.0.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_visit_token: Optional[str] = None
_visit_exp: float = 0.0
_active_host: str = HOSTS[0]


def _ua() -> Dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
    }


def _is_blocked_html(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return (
        t.startswith("<!")
        or t.startswith("<html")
        or "just a moment" in t
        or "cf-browser-verification" in t
        or "challenge-platform" in t
    )


def _hmac_headers(path: str) -> Dict[str, str]:
    """Sesuai scraper komunitas: key = ts+GET+path+ACCESS+SECRET, msg = wm-api-request."""
    ts = str(int(time.time()))
    key_material = f"{ts}GET{path}{ACCESS_KEY}{SECRET_KEY}"
    sig = hmac.new(
        key_material.encode("utf-8"),
        b"wm-api-request",
        hashlib.sha256,
    ).hexdigest()
    return {
        "x-wm-accses-key": ACCESS_KEY,  # typo hulu (bukan 'access')
        "x-wm-request-time": ts,
        "x-wm-request-signature": sig,
    }


async def _ensure_visit_token(force: bool = False) -> Optional[str]:
    global _visit_token, _visit_exp, _active_host
    now = time.time()
    if not force and _visit_token and now < (_visit_exp - 300):
        return _visit_token

    for host in HOSTS:
        if "mantweh" in host:
            continue
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                r = await client.get(
                    f"{host}/api/visit-token",
                    headers={**_ua(), "Origin": host, "Referer": host + "/"},
                )
            text = r.text or ""
            if r.status_code != 200 or _is_blocked_html(text):
                continue
            data = r.json()
            token = data.get("token")
            if data.get("status") and token:
                _visit_token = str(token)
                exp = float(data.get("expiresAt") or data.get("expires_at") or 0)
                if exp > 1e12:  # ms
                    _visit_exp = exp / 1000.0
                elif exp > 0:
                    _visit_exp = exp
                else:
                    _visit_exp = now + 3600
                _active_host = host
                return _visit_token
        except Exception:
            continue
    return _visit_token


async def api_get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    global _active_host
    relative = path if path.startswith("/") else f"/{path}"
    # jangan double-encode path segments yang sudah di-quote pemanggil
    hosts = [_active_host] + [h for h in HOSTS if h != _active_host]
    last_err: Optional[Exception] = None

    for host in hosts:
        use_hmac = "mantweh" in host
        origin = HOSTS[0] if use_hmac else host
        headers: Dict[str, str] = {
            **_ua(),
            "Origin": origin,
            "Referer": origin + "/",
        }
        if use_hmac:
            headers.update(_hmac_headers(relative))
        else:
            token = await _ensure_visit_token()
            if token:
                headers["X-WM-Visit"] = token

        # bersihkan params None
        clean_params = None
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}

        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                    r = await client.get(
                        f"{host}{relative}",
                        headers=headers,
                        params=clean_params,
                    )
                text = r.text or ""
                if _is_blocked_html(text):
                    last_err = HTTPException(
                        403,
                        f"Cloudflare memblokir {host}. Coba jaringan/browser yang lolos CF.",
                    )
                    if not use_hmac and attempt == 0:
                        tok = await _ensure_visit_token(force=True)
                        if tok:
                            headers["X-WM-Visit"] = tok
                        continue
                    break

                if r.status_code >= 400:
                    last_err = HTTPException(
                        r.status_code,
                        f"Westmanga HTTP {r.status_code} @ {host}{relative}",
                    )
                    if r.status_code in (401, 403) and not use_hmac and attempt == 0:
                        tok = await _ensure_visit_token(force=True)
                        if tok:
                            headers["X-WM-Visit"] = tok
                        continue
                    break

                try:
                    data = r.json()
                except Exception:
                    last_err = HTTPException(502, f"Bukan JSON dari {host}")
                    break

                _active_host = host
                return data
            except HTTPException as e:
                last_err = e
            except Exception as e:
                last_err = HTTPException(502, f"{type(e).__name__}: {e}")

    if isinstance(last_err, HTTPException):
        raise last_err
    if last_err:
        raise HTTPException(502, str(last_err))
    raise HTTPException(502, "Westmanga unreachable")


def _abs_url(raw: str, host: Optional[str] = None) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if re.match(r"^https?://", raw, re.I):
        return raw
    base = (host or _active_host or HOSTS[0]).rstrip("/")
    return f"{base}{raw if raw.startswith('/') else '/' + raw}"


def _norm_type(t: Any) -> str:
    s = str(t or "").strip().lower()
    if not s:
        return "MANGA"
    if "manhwa" in s or s in ("kr", "korea", "korean"):
        return "MANHWA"
    if "manhua" in s or s in ("cn", "china", "chinese"):
        return "MANHUA"
    if "comic" in s or s in ("western", "barat"):
        return "COMIC"
    if "manga" in s or s in ("jp", "japan", "japanese", "comic"):
        return "MANGA"
    return str(t).upper()[:14]


def normalize_item(it: dict) -> dict:
    slug = str(it.get("slug") or it.get("id") or "").strip()
    if "/comic/" in slug:
        slug = slug.rstrip("/").split("/comic/")[-1]
    title = str(it.get("title") or it.get("name") or slug).strip()
    cover = _abs_url(str(it.get("cover") or it.get("thumbnail") or it.get("image") or ""))
    return {
        "judul": title,
        "title": title,
        "slug": slug,
        "thumbnail": cover,
        "sampul": cover,
        "type": _norm_type(it.get("type") or it.get("content_type") or it.get("country_id")),
        "status": str(it.get("status") or ""),
        "url": f"{_active_host}/comic/{slug}" if slug else "",
        "source": "westmanga",
    }


def normalize_chapter(c: dict, series_slug: str = "") -> dict:
    ch_slug = str(c.get("slug") or c.get("chapter_slug") or c.get("id") or "").strip()
    if "/chapter/" in ch_slug:
        ch_slug = ch_slug.rstrip("/").split("/chapter/")[-1]
    number = str(c.get("number") or c.get("chapter") or "").strip()
    if not number and ch_slug:
        m = re.search(r"(\d+(?:\.\d+)?)", ch_slug)
        if m:
            number = m.group(1)
    title = str(c.get("title") or c.get("name") or (f"Chapter {number}" if number else ch_slug))
    return {
        "judul": title,
        "title": title,
        "slug": ch_slug,
        "number": number,
        "id": ch_slug,
        "url": (
            f"{_active_host}/comic/{series_slug}/chapter/{ch_slug}"
            if series_slug and ch_slug
            else ""
        ),
    }


def _rows_from(data: Any) -> List[dict]:
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


@app.get("/")
async def root():
    return {
        "status": "online",
        "version": "1.0.1",
        "author": "Nat",
        "active_host": _active_host,
        "hosts": HOSTS,
        "endpoints": {
            "latest": "/api/latest?page=1",
            "search": "/api/search?q=",
            "detail": "/api/detail/{slug}",
            "chapter": "/api/chapter?chapter=",
            "projects": "/api/projects?page=1",
        },
        "note": "Cloudflare sering 403 dari IP datacenter/Vercel.",
    }


@app.get("/health")
async def health():
    return {
        "status": "online",
        "app": "Westmanga-API-By-Nat",
        "version": "1.0.1",
        "active_host": _active_host,
        "has_visit_token": bool(_visit_token),
    }


@app.get("/api/latest")
async def api_latest(
    page: int = Query(1, ge=1),
    project: bool = Query(False),
    order_by: str = Query("Update", alias="orderBy"),
):
    params = {
        "page": page,
        "per_page": PER_PAGE,
        "type": "Comic",
        "project": "true" if project else "false",
        "orderBy": order_by or "Update",
    }
    try:
        data = await api_get("/api/contents", params)
        items = [normalize_item(x) for x in _rows_from(data) if x.get("slug") or x.get("id")]
    except HTTPException:
        items = []
        data = None
        if not project:
            # fallback seperti scraper: coba project=true
            data = await api_get(
                "/api/contents",
                {
                    "page": page,
                    "per_page": PER_PAGE,
                    "type": "Comic",
                    "project": "true",
                },
            )
            items = [
                normalize_item(x)
                for x in _rows_from(data)
                if x.get("slug") or x.get("id")
            ]
        else:
            raise
    # jika list kosong tanpa exception, coba project sekali
    if not items and not project:
        try:
            data2 = await api_get(
                "/api/contents",
                {
                    "page": page,
                    "per_page": PER_PAGE,
                    "type": "Comic",
                    "project": "true",
                },
            )
            items = [
                normalize_item(x)
                for x in _rows_from(data2)
                if x.get("slug") or x.get("id")
            ]
        except Exception:
            pass
    return {
        "status": True,
        "page": page,
        "total": len(items),
        "data": items,
        "source": "westmanga",
        "host": _active_host,
    }


@app.get("/api/projects")
async def api_projects(page: int = Query(1, ge=1)):
    return await api_latest(page=page, project=True, order_by="Update")


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=1), page: int = Query(1, ge=1)):
    q = q.strip()
    data = await api_get(
        "/api/contents",
        {"q": q, "page": page, "per_page": PER_PAGE, "type": "Comic"},
    )
    items = [normalize_item(x) for x in _rows_from(data) if x.get("slug") or x.get("id")]
    return {
        "status": True,
        "query": q,
        "page": page,
        "total": len(items),
        "data": items,
        "source": "westmanga",
    }


@app.get("/api/detail/{slug}")
async def api_detail(slug: str):
    slug = slug.strip().strip("/")
    if "/comic/" in slug:
        slug = slug.split("/comic/")[-1]
    slug = slug.split("/")[0]
    if not slug:
        raise HTTPException(400, "slug wajib")

    data = await api_get(f"/api/comic/{slug}")
    d = data.get("data") if isinstance(data, dict) else data
    if not isinstance(d, dict):
        raise HTTPException(404, f"Series tidak ditemukan: {slug}")

    s = str(d.get("slug") or slug)
    chapters = [
        normalize_chapter(c, s)
        for c in (d.get("chapters") or d.get("chapter_list") or [])
        if isinstance(c, dict)
    ]

    def chap_key(c: dict) -> float:
        try:
            return float(c.get("number") or 0)
        except Exception:
            return -1.0

    chapters = sorted(chapters, key=chap_key, reverse=True)
    cover = _abs_url(str(d.get("cover") or d.get("thumbnail") or ""))
    genres = []
    for g in d.get("genres") or []:
        name = str(g.get("name") if isinstance(g, dict) else g).strip()
        if name and name not in genres:
            genres.append(name)

    return {
        "status": True,
        "data": {
            "judul": str(d.get("title") or s),
            "title": str(d.get("title") or s),
            "slug": s,
            "thumbnail": cover,
            "sampul": cover,
            "sinopsis": str(
                d.get("sinopsis") or d.get("synopsis") or d.get("description") or ""
            ),
            "type": _norm_type(d.get("type") or d.get("content_type") or d.get("country_id")),
            "status": str(d.get("status") or ""),
            "genres": genres,
            "chapters": chapters,
            "total_chapter": len(chapters),
            "source": "westmanga",
            "url": f"{_active_host}/comic/{s}",
        },
    }


@app.get("/api/chapter")
async def api_chapter(
    chapter: str = Query(..., min_length=1, description="chapter slug mis. chapter-10"),
    slug: str = Query("", description="series slug (opsional)"),
):
    ch = chapter.strip().strip("/")
    if "/chapter/" in ch:
        ch = ch.split("/chapter/")[-1]
    if not ch:
        raise HTTPException(400, "chapter wajib")

    # path segment: biarkan httpx / server handle encoding; slug aman
    safe = ch.replace("..", "")
    data = await api_get(f"/api/v/{safe}")
    d = data.get("data") if isinstance(data, dict) else data

    images: List[str] = []
    raw: Any = None
    if isinstance(d, dict):
        raw = d.get("images") or d.get("pages") or d.get("image")
    elif isinstance(d, list):
        raw = d
    elif isinstance(data, dict):
        raw = data.get("images") or data.get("pages")

    if isinstance(raw, list):
        for u in raw:
            if isinstance(u, str):
                uu = _abs_url(u)
                if uu.startswith("http"):
                    images.append(uu)
            elif isinstance(u, dict):
                uu = _abs_url(str(u.get("url") or u.get("src") or u.get("image") or ""))
                if uu.startswith("http"):
                    images.append(uu)
    elif isinstance(raw, str):
        uu = _abs_url(raw)
        if uu.startswith("http"):
            images.append(uu)

    # dedupe preserve order
    seen = set()
    images = [x for x in images if not (x in seen or seen.add(x))]

    if not images:
        raise HTTPException(404, "Gambar chapter kosong atau diblokir sumber")

    return {
        "status": True,
        "slug": slug,
        "chapter": ch,
        "total": len(images),
        "images": images,
        "gambar": images,
        "source": "westmanga",
        "host": _active_host,
    }


handler = Mangum(app)
