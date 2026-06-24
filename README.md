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
| POST   | `/api/remove`  | Delete **all** rows for a `player_hash`. Succeeds on zero rows. Doubles as takedown-by-hash. Requires `X-Client-Token`. |
| GET    | `/api/wall`    | Feats with ≥1 global unlock + unlocker list (name + short hash) + count. Cold start → `200 []`. Short-TTL cached. |
| GET    | `/feats.json`  | Canonical catalogue. `secret` Feats hide their description until first global unlock. |
| GET    | `/`            | Static read-only wall page. |

## Run locally

```sh
pip install -r requirements.txt
DATA_DIR=./data ACHIEVEMENTS_CLIENT_TOKEN=dev uvicorn server:app --reload --no-access-log
```

Tests: `pip install -r requirements-test.txt && pytest -q`.

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
