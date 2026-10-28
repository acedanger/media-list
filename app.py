"""media-list: serve an MDBList list in formats Sonarr and Radarr can import.

Endpoints
  /             human-readable HTML table of the list
  /sonarr.json  Sonarr v4 "Custom List" format:  [{"tvdbId": 123, "title": ...}]
  /radarr.json  Radarr "Custom List" format:     [{"id": <tmdb>, "imdb_id": ..., "title": ...}]
  /items.json   the cached MDBList response (movies + shows)
  /healthz      liveness probe

Only the Python standard library is used.
"""

import html
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

log = logging.getLogger("media-list")


def fetch_list():
    """Fetch every item in the list, following cursor pagination."""
    movies, shows = [], []
    cursor = None
    while True:
        params = {"sort": SORT, "order": ORDER, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        url = f"{API_BASE}/lists/{LIST}/items?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {API_KEY}",
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


class Cache:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = None
        self.fetched_at = 0.0

    def get(self):
        with self.lock:
            if self.data is None or time.time() - self.fetched_at > CACHE_TTL:
                try:
                    self.data = fetch_list()
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


def tmdb_id(item):
    return (item.get("ids") or {}).get("tmdb") or item.get("id")


def sonarr_items(data):
    return [{"tvdbId": s["tvdb_id"], "title": s.get("title")}
            for s in data["shows"] if s.get("tvdb_id")]


def radarr_items(data):
    return [{"id": tmdb_id(m), "imdb_id": m.get("imdb_id"), "title": m.get("title")}
            for m in data["movies"] if tmdb_id(m)]


def render_html(data):
    rows = []
    for item in data["shows"] + data["movies"]:
        imdb = item.get("imdb_id") or ""
        title = html.escape(item.get("title") or "")
        if imdb:
            title = f'<a href="https://www.imdb.com/title/{html.escape(imdb)}/">{title}</a>'
        rows.append(
            f"<tr><td>{item.get('rank', '')}</td><td>{title}</td>"
            f"<td>{item.get('release_year') or ''}</td><td>{item.get('mediatype', '')}</td>"
            f"<td>{item.get('tvdb_id') or ''}</td><td>{tmdb_id(item) or ''}</td></tr>"
        )
    updated = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(cache.fetched_at))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(LIST)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: system-ui, sans-serif; margin: 1rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ text-align: left; padding: .3rem .6rem; border-bottom: 1px solid #8884; }}
  code {{ font-size: .9em; }}
</style></head><body>
<h1>{html.escape(LIST)}</h1>
<p>{len(data["shows"])} shows, {len(data["movies"])} movies &middot;
sorted by <code>{html.escape(SORT)}</code> ({html.escape(ORDER)}) &middot; updated {updated}</p>
<p>Sonarr: <a href="sonarr.json"><code>/sonarr.json</code></a> &middot;
Radarr: <a href="radarr.json"><code>/radarr.json</code></a> &middot;
Raw: <a href="items.json"><code>/items.json</code></a></p>
<table><thead><tr><th>#</th><th>Title</th><th>Year</th><th>Type</th><th>TVDB</th><th>TMDB</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "media-list/1.0"

    def send(self, status, body, content_type):
        payload = body.encode()
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
        routes = {
            "/": lambda d: self.send(200, render_html(d), "text/html; charset=utf-8"),
            "/sonarr.json": lambda d: self.send_json(sonarr_items(d)),
            "/radarr.json": lambda d: self.send_json(radarr_items(d)),
            "/items.json": lambda d: self.send_json(d),
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

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not API_KEY:
        raise SystemExit("MDBLIST_API_KEY is not set")
    log.info("serving %s on :%d (cache %ds)", LIST, PORT, CACHE_TTL)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
