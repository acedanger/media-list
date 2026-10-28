"""media-list: serve an MDBList list in formats Sonarr and Radarr can import.

Endpoints
  /                   HTML page: the list, Sonarr status, bulk remove
  /sonarr.json        Sonarr v4 "Custom List" format:  [{"tvdbId": 123, "title": ...}]
  /radarr.json        Radarr "Custom List" format:     [{"id": <tmdb>, "imdb_id": ..., "title": ...}]
  /items.json         the cached MDBList response (movies + shows)
  /api/shows          list shows merged with Sonarr status/stats (used by the page)
  /api/sonarr/remove  POST {"seriesIds": [...], "deleteFiles": bool}
  /healthz            liveness probe

Only the Python standard library is used.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_BASE = "https://api.mdblist.com"
API_KEY = os.environ.get("MDBLIST_API_KEY", "")
LIST = os.environ.get("MDBLIST_LIST", "ndg3270/apple-tv-series").strip("/")
SORT = os.environ.get("MDBLIST_SORT", "score_average")
ORDER = os.environ.get("MDBLIST_ORDER", "asc")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "3600"))
PORT = int(os.environ.get("PORT", "8080"))
SONARR_URL = os.environ.get("SONARR_URL", "http://host.docker.internal:8989").rstrip("/")
SONARR_API_KEY = os.environ.get("SONARR_API_KEY", "")

INDEX_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

log = logging.getLogger("media-list")


def fetch_list():
    """Fetch every item in the list, following cursor pagination."""
    movies, shows = [], []
    cursor = None
    while True:
        # MDBList API keys go in ?apikey=; the Bearer header is only for OAuth tokens.
        params = {"apikey": API_KEY, "sort": SORT, "order": ORDER, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        url = f"{API_BASE}/lists/{LIST}/items?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "media-list/1.0",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        movies.extend(data.get("movies") or [])
        shows.extend(data.get("shows") or [])
        pagination = data.get("pagination") or {}
        cursor = pagination.get("next_cursor") or data.get("next_cursor")
        if not cursor:
            break
    return {"movies": movies, "shows": shows}


def fetch_show_info(tmdb_ids):
    """Return {tmdb_id: {"status", "score_average", "ids"}} via MDBList's batch media lookup."""
    info = {}
    for i in range(0, len(tmdb_ids), 100):
        req = urllib.request.Request(
            f"{API_BASE}/tmdb/show?{urllib.parse.urlencode({'apikey': API_KEY})}",
            data=json.dumps({"ids": tmdb_ids[i:i + 100]}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "media-list/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            for item in json.load(resp):
                tmdb = (item.get("ids") or {}).get("tmdb")
                if tmdb:
                    info[tmdb] = {"status": item.get("status"), "score_average": item.get("score_average"),
                                  "ids": item.get("ids") or {}}
    return info


class Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = None
        self.fetched_at = 0.0

    def get(self):
        with self.lock:
            if self.data is None or time.time() - self.fetched_at > CACHE_TTL:
                try:
                    data = fetch_list()
                    try:
                        data["show_info"] = fetch_show_info(
                            [t for t in (tmdb_id(s) for s in data["shows"]) if t])
                    except (urllib.error.URLError, ValueError) as exc:
                        # Status and extra IDs are nice-to-have; keep the list without them.
                        log.error("show info lookup failed: %s", exc)
                        data["show_info"] = (self.data or {}).get("show_info", {})
                    self.data = data
                    self.fetched_at = time.time()
                    log.info("refreshed %s: %d movies, %d shows", LIST,
                             len(self.data["movies"]), len(self.data["shows"]))
                except (urllib.error.URLError, ValueError) as exc:
                    # Keep serving stale data if we have it.
                    log.error("refresh failed: %s", exc)
                    if self.data is None:
                        raise
            return self.data


cache = Cache()


def sonarr(method, path, params=None):
    """Call the Sonarr v3 API (also used by Sonarr v4)."""
    url = f"{SONARR_URL}/api/v3{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers={
        "X-Api-Key": SONARR_API_KEY,
        "Accept": "application/json",
        "User-Agent": "media-list/1.0",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def sonarr_state():
    """Return ({tvdbId: series}, {excluded tvdbIds}, {seriesId: queue summary}) from Sonarr."""
    series = {s["tvdbId"]: s for s in sonarr("GET", "/series") if s.get("tvdbId")}
    try:
        excluded = {e["tvdbId"] for e in sonarr("GET", "/importlistexclusion") if e.get("tvdbId")}
    except urllib.error.HTTPError as exc:
        log.warning("could not read Sonarr list exclusions: %s", exc)
        excluded = set()
    try:
        queue = summarize_queue(fetch_queue())
    except (urllib.error.URLError, ValueError) as exc:
        log.warning("could not read Sonarr queue: %s", exc)
        queue = {}
    return series, excluded, queue


def fetch_queue():
    """Return every record in Sonarr's download queue."""
    records, page = [], 1
    while True:
        data = sonarr("GET", "/queue", {"page": page, "pageSize": 1000,
                                       "includeUnknownSeriesItems": "false"})
        records.extend(data.get("records") or [])
        if len(records) >= data.get("totalRecords", 0) or not data.get("records"):
            return records
        page += 1


IMPORT_STATES = {"importPending", "importing", "importBlocked"}


def summarize_queue(records):
    """Group queue records by series: counts, progress, latest ETA and any problems."""
    summary = {}
    for r in records:
        sid = r.get("seriesId")
        if sid is None:
            continue
        q = summary.setdefault(sid, {"count": 0, "downloading": 0, "importing": 0, "problems": 0,
                                     "size": 0, "sizeleft": 0, "eta": None, "messages": []})
        q["count"] += 1
        q["size"] += r.get("size") or 0
        q["sizeleft"] += r.get("sizeleft") or 0
        if r.get("status") == "downloading":
            q["downloading"] += 1
        if r.get("trackedDownloadState") in IMPORT_STATES:
            q["importing"] += 1
        if r.get("trackedDownloadStatus") in ("warning", "error") or r.get("status") in ("failed", "warning"):
            q["problems"] += 1
            for m in r.get("statusMessages") or []:
                q["messages"].extend(m.get("messages") or [m.get("title")])
        eta = r.get("estimatedCompletionTime")
        if eta and (q["eta"] is None or eta > q["eta"]):
            q["eta"] = eta
    for q in summary.values():
        q["messages"] = sorted({m for m in q["messages"] if m})[:5]
    return summary


def tmdb_id(item):
    return (item.get("ids") or {}).get("tmdb") or item.get("id")


def sonarr_items(data):
    return [{"tvdbId": s["tvdb_id"], "title": s.get("title")}
            for s in data["shows"] if s.get("tvdb_id")]


def radarr_items(data):
    return [{"id": tmdb_id(m), "imdb_id": m.get("imdb_id"), "title": m.get("title")}
            for m in data["movies"] if tmdb_id(m)]


def shows_with_sonarr(data):
    """Merge list shows with their Sonarr status for the page."""
    result = {
        "list": LIST, "sort": SORT, "order": ORDER,
        "updated": cache.fetched_at, "sonarr": {"configured": bool(SONARR_API_KEY)},
    }
    series, excluded, queue = {}, set(), {}
    if SONARR_API_KEY:
        try:
            series, excluded, queue = sonarr_state()
            result["sonarr"]["ok"] = True
        except (urllib.error.URLError, ValueError) as exc:
            log.error("Sonarr request failed: %s", exc)
            result["sonarr"].update(ok=False, error=str(exc))

    shows = []
    show_info = data.get("show_info", {})
    # data["shows"] is already in MDBList's SORT/ORDER; "position" preserves that,
    # while "rank" is the curator's order in the list itself.
    for position, item in enumerate(data["shows"], 1):
        tvdb = item.get("tvdb_id")
        s = series.get(tvdb)
        info = show_info.get(tmdb_id(item), {})
        ids = {"imdb": item.get("imdb_id"), "tmdb": tmdb_id(item), "tvdb": tvdb}
        ids.update({k: v for k, v in (info.get("ids") or {}).items() if v})
        row = {
            "position": position, "rank": item.get("rank"), "title": item.get("title"),
            "year": item.get("release_year"), "imdb": item.get("imdb_id"),
            "ids": ids, "score": info.get("score_average"),
            # TMDB's status (Returning Series, Ended, Canceled, ...), else Sonarr's.
            "showStatus": info.get("status") or (s.get("status", "").title() if s else None),
            "status": "in_sonarr" if s else ("excluded" if tvdb in excluded else "missing"),
        }
        if s:
            stats = s.get("statistics") or {}
            row["sonarr"] = {
                "id": s["id"], "titleSlug": s.get("titleSlug"),
                "monitored": s.get("monitored"), "seriesStatus": s.get("status"),
                "episodeFileCount": stats.get("episodeFileCount", 0),
                "episodeCount": stats.get("episodeCount", 0),
                "sizeOnDisk": stats.get("sizeOnDisk", 0),
                "queue": queue.get(s["id"]),
            }
        shows.append(row)
    result["shows"] = shows
    return result


def remove_series(series_ids, delete_files):
    """Delete series from Sonarr, adding each to Sonarr's import list exclusions."""
    results = []
    for sid in series_ids:
        try:
            sonarr("DELETE", f"/series/{int(sid)}", {
                "deleteFiles": str(bool(delete_files)).lower(),
                "addImportListExclusion": "true",
            })
            log.info("removed Sonarr series %s (deleteFiles=%s)", sid, bool(delete_files))
            results.append({"id": sid, "ok": True})
        except (urllib.error.URLError, ValueError) as exc:
            log.error("failed to remove Sonarr series %s: %s", sid, exc)
            results.append({"id": sid, "ok": False, "error": str(exc)})
    return results


class Handler(BaseHTTPRequestHandler):
    server_version = "media-list/1.0"

    def send(self, status, body, content_type):
        payload = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def send_json(self, obj, status=200):
        self.send(status, json.dumps(obj), "application/json")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            return self.send_json({"ok": True})
        if path == "/":
            with open(INDEX_HTML, "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        routes = {
            "/sonarr.json": lambda d: self.send_json(sonarr_items(d)),
            "/radarr.json": lambda d: self.send_json(radarr_items(d)),
            "/items.json": lambda d: self.send_json(d),
            "/api/shows": lambda d: self.send_json(shows_with_sonarr(d)),
        }
        route = routes.get(path)
        if route is None:
            return self.send_json({"error": "not found"}, 404)
        try:
            data = cache.get()
        except Exception as exc:
            return self.send_json({"error": f"failed to fetch list: {exc}"}, 502)
        route(data)

    do_HEAD = do_GET

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        if path != "/api/sonarr/remove":
            return self.send_json({"error": "not found"}, 404)
        # Requiring JSON forces a CORS preflight, so other sites can't submit this cross-origin.
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self.send_json({"error": "Content-Type must be application/json"}, 415)
        if not SONARR_API_KEY:
            return self.send_json({"error": "SONARR_API_KEY is not set"}, 503)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            ids = [int(i) for i in body["seriesIds"]]
        except (ValueError, KeyError, TypeError):
            return self.send_json({"error": "expected {\"seriesIds\": [int, ...]}"}, 400)
        results = remove_series(ids, body.get("deleteFiles", False))
        status = 200 if all(r["ok"] for r in results) else 502
        self.send_json({"results": results}, status)

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not API_KEY:
        raise SystemExit("MDBLIST_API_KEY is not set")
    if not SONARR_API_KEY:
        log.warning("SONARR_API_KEY is not set; Sonarr features are disabled")
    log.info("serving %s on :%d (cache %ds, sonarr %s)", LIST, PORT, CACHE_TTL, SONARR_URL)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
