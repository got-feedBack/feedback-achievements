# feedback-achievements — Feats wall

A small, self-contained, multi-user service that displays **Feats of Power**: a
chosen display name plus which generic activity milestones a player has earned.
Cooperative and non-ranked; hidden until the first global unlock.

This is the hosted companion to the self-hosted feedBack app's bundled
`achievements` plugin. The app is single-user with no shared backend, so the
multi-user wall lives here as a separate service.

## What it carries — and what it never does

- **Feats only.** The catalogue (`feats.json`) is the allow-list; any id not in
  it is rejected at `POST /api/unlock`. There are **no** song titles, library
  data, audio, scores, or competency/skill claims anywhere in this service.
- **No IP retention.** No table has an IP column, and access logging is disabled
  (`uvicorn --no-access-log`). Client IPs are used only transiently, in memory,
  for rate-limiting, and are never written anywhere.
- Trust model: client-authoritative + light anti-abuse (baked-in client token,
  per-(hash, IP) in-memory rate limit, display-name profanity filter). Feat
  spoofing is accepted as low-stakes; impersonation is handled by the
  takedown-by-hash path.

## Endpoints

| Method | Path           | Purpose |
|--------|----------------|---------|
| POST   | `/api/unlock`  | Record a Feat unlock `{display_name, player_hash, achievement_id, unlocked_at}`. Validates id ∈ catalogue, profanity-filters the name, rate-limits, upserts. Requires `X-Client-Token`. |
| POST   | `/api/remove`  | Delete **all** rows for a `player_hash` (exact). Succeeds on zero rows. Requires `X-Client-Token`. With `X-Admin-Token` (= `ACHIEVEMENTS_ADMIN_TOKEN`, a real secret) it also accepts a **hash prefix** — i.e. the 6-char short hash the wall displays — which is the moderator takedown path. Ambiguous prefixes → `409`. Without the admin token a prefix matches nothing, so a copied short hash can't be used to delete other players. |
| GET    | `/api/wall`    | Feats with ≥1 global unlock + unlocker list (name + short hash) + count. Cold start → `200 []`. Short-TTL cached. |
| GET    | `/feats.json`  | Canonical catalogue. `secret` Feats hide their description until first global unlock. |
| GET    | `/`            | Static read-only wall page. |

## Run locally

```sh
pip install -r requirements.txt
DATA_DIR=./data ACHIEVEMENTS_CLIENT_TOKEN=dev uvicorn server:app --reload --no-access-log
```

Tests: `pip install -r requirements-test.txt && pytest -q`.

## Configuration

Environment variables read by `server.py`:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATA_DIR` | `data/` next to `server.py` | Directory for the SQLite database (`wall.db`). Created if missing. |
| `ACHIEVEMENTS_CLIENT_TOKEN` | `fb-wall-v1` | Value `/api/unlock` and `/api/remove` expect in `X-Client-Token`. The code treats it as a baked-in obfuscation token, not a secret. |
| `ACHIEVEMENTS_ADMIN_TOKEN` | (unset) | A real secret. When set, `/api/remove` with a matching `X-Admin-Token` also accepts a hash prefix. Unset means exact-hash deletes only. |
| `RATE_MAX` | `30` | Max unlock POSTs per (player hash, IP) per window. |
| `RATE_WINDOW_S` | `60` | Rate-limit window, in seconds. |

`migrate.py` reads two more:

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | (required) | Postgres target. Same as `--database-url`. |
| `SQLITE_PATH` | `data/wall.db` | SQLite source, relative to the working directory. Same as `--sqlite`. |

## Deploy (Render) — operability must-fixes (gating)

`render.yaml` is a ready blueprint. Three constraints are **not optional**:

1. **Single instance.** The rate-limiter is in-memory and SQLite lives on a
   persistent disk — `numInstances: 1`, no autoscaling. Running more than one
   instance splits state.
2. **No IP in logs.** The start command keeps `--no-access-log`. Do not remove
   it. (IPs are never stored in tables either.)
3. **Migration tool.** If you move to managed Postgres, run `migrate.py`
   (`DATABASE_URL=… python migrate.py`) and keep the single-instance pin until
   the rate-limit moves to a shared store.

License: AGPL-3.0. Public material is neutral — this project has nothing to do
with any specific game, and no game titles appear anywhere in it.
