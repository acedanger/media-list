# media-list

Fetches an [MDBList](https://mdblist.com) list through the MDBList API and serves it
in formats Sonarr and Radarr can import as custom lists. Exposed on the tailnet via
the [DockTail](https://github.com/marvinvr/docktail) sidecar.

Default list: [ndg3270/apple-tv-series](https://mdblist.com/lists/ndg3270/apple-tv-series),
sorted by `score_average`.

## Endpoints

| Path           | Purpose                                             |
| -------------- | --------------------------------------------------- |
| `/`            | HTML table of the list                              |
| `/sonarr.json` | Sonarr v4 Custom List (`[{"tvdbId": …}]`)            |
| `/radarr.json` | Radarr Custom List (`[{"id": <tmdb>, "imdb_id": …}]`) |
| `/items.json`  | Raw MDBList response (movies + shows)               |
| `/healthz`     | Health check                                        |

Results are cached for `CACHE_TTL` seconds (default 3600). If a refresh fails, the
last good copy keeps being served.

## Run

```bash
cp .env.example .env   # fill in Tailscale OAuth creds and MDBLIST_API_KEY
docker compose up -d --build
```

Then, from any tailnet device: `curl http://media-list.<your-tailnet>.ts.net/sonarr.json`

## Sonarr / Radarr

- **Sonarr** → Settings → Import Lists → `+` → **Custom List** → List URL
  `http://media-list.<your-tailnet>.ts.net/sonarr.json`
- **Radarr** → Settings → Lists → `+` → **Custom List** → List URL
  `http://media-list.<your-tailnet>.ts.net/radarr.json`

The host running Sonarr/Radarr must be on the tailnet (or route to it).

## Configuration

| Variable          | Default                   |
| ----------------- | ------------------------- |
| `MDBLIST_API_KEY` | *(required)*              |
| `MDBLIST_LIST`    | `ndg3270/apple-tv-series` |
| `MDBLIST_SORT`    | `score_average`           |
| `MDBLIST_ORDER`   | `asc`                     |
| `CACHE_TTL`       | `3600`                    |
| `PORT`            | `8080`                    |
