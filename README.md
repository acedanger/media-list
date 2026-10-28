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

Then, from any tailnet device: `curl http://media-list.whale-woodpecker.ts.net/sonarr.json`

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

### Tailscale OAuth

The application uses Tailscale OAuth for authentication. You need to provide the `TAILSCALE_OAUTH_CLIENT_ID` and `TAILSCALE_OAUTH_CLIENT_SECRET` in the `.env` file.

See [DockTail Tailscale Admin OAuth Credentials](https://docktail.org/docs/tailscale-admin/#oauth-credentials) for more information.

```plaintext
OAuth is recommended. It enables automatic service creation and avoids expiring API keys.

Open Tailscale Admin Console -> Settings -> Trust credentials.

Create an OAuth client scoped to your server tag, for example tag:server.

Grant these permissions:

General -> Services: Write (and specify required tag)
Devices -> Core: Write
Keys -> Auth Keys: Write (only when using the sidecar method)
```