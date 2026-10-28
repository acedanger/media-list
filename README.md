# media-list

Fetches an [MDBList](https://mdblist.com) list through the MDBList API and serves it
in formats Sonarr and Radarr can import as custom lists. Exposed on the tailnet via
the [DockTail](https://github.com/marvinvr/docktail) sidecar.

Default list: [ndg3270/apple-tv-series](https://mdblist.com/lists/ndg3270/apple-tv-series),
sorted by `score_average`.

## Endpoints

| Path           | Purpose                                             |
| -------------- | --------------------------------------------------- |
| `/`            | The list with Sonarr status; filter, sort, bulk remove |
| `/sonarr.json` | Sonarr v4 Custom List (`[{"tvdbId": …}]`)            |
| `/radarr.json` | Radarr Custom List (`[{"id": <tmdb>, "imdb_id": …}]`) |
| `/items.json`  | Raw MDBList response (movies + shows)               |
| `/api/shows`   | List shows merged with Sonarr status (used by `/`)  |
| `/api/sonarr/remove` | `POST {"seriesIds": [..], "deleteFiles": bool}` |
| `/healthz`     | Health check                                        |

Results are cached for `CACHE_TTL` seconds (default 3600). If a refresh fails, the
last good copy keeps being served.

## Sonarr management

With `SONARR_API_KEY` set, the page shows each show's Sonarr status (**In Sonarr**,
**Not in Sonarr**, or **Excluded** via Sonarr's import list exclusions), episode
counts and size on disk, and lets you remove selected shows from Sonarr.

Removing a show always adds it to Sonarr's import list exclusions, so the Custom
List doesn't add it back on the next sync. Deleting files from disk is opt-in per
removal. Exclusions can be undone in Sonarr → Settings → Import Lists → Exclusions.

The app reaches Sonarr at `SONARR_URL` (default `http://host.docker.internal:8989`,
i.e. Sonarr's published port on the Docker host).

Anyone who can reach this service on the tailnet can remove shows, so scope the
Tailscale grant for `svc:media-list` accordingly.

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
| `SONARR_API_KEY`  | *(optional; enables Sonarr features)* |
| `SONARR_URL`      | `http://host.docker.internal:8989` |

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