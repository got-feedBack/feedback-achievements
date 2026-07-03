"""feedback-achievements — hosted Feats wall (Component B).

A standalone, multi-user service that displays **Feats of Power** unlocks: a
chosen display name + which generic activity milestones a player has earned.
Cooperative, non-ranked, hidden until the first global unlock.

Hard boundaries (legal / DMCA §1204):
  * Feats ONLY — never songs, libraries, audio, titles, or competency/skill data.
  * The catalogue (feats.json) is the allow-list; anything else is rejected.
  * **No IP is ever stored** — not in tables, and access logging is disabled at
    the uvicorn layer (see render.yaml start command / README).

Client-authoritative + light anti-abuse: a baked-in client token (obfuscation,
not security), per-(hash, IP) in-memory rate-limit, and a display-name profanity
filter. Feat-unlock spoofing is accepted as low-stakes; impersonation is handled
by the takedown-by-hash path (POST /api/remove).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR / "data")))
DB_PATH = DATA_DIR / "wall.db"
# Baked-in obfuscation token (NOT a secret). Override via env in deployment.
CLIENT_TOKEN = os.environ.get("ACHIEVEMENTS_CLIENT_TOKEN", "fb-wall-v1")
# In-memory rate limit: max unlock POSTs per (hash, ip) per window.
RATE_MAX = int(os.environ.get("RATE_MAX", "30"))
RATE_WINDOW_S = int(os.environ.get("RATE_WINDOW_S", "60"))
WALL_CACHE_TTL_S = 5

app = FastAPI(title="feedback-achievements (Feats wall)")
# Read-only CORS so the marketing site (got-feedback.org) can render the wall
# in-page. GET only — unlock/remove stay same-origin + token-gated as before.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^https://(www\.)?got-feedback\.org$"
        r"|^https://feedback-website-h5ds\.onrender\.com$"
        r"|^http://localhost(:\d+)?$"
    ),
    allow_methods=["GET"],
    allow_headers=[],
)
_lock = threading.Lock()
_rate: dict[tuple[str, str], list[float]] = {}
# Sweep fully-expired rate buckets once the dict crosses this size so a
# long-lived single instance can't leak one permanent entry per (hash, ip).
_RATE_GC_THRESHOLD = 5000
# Wall cache is version-gated: every write bumps _data_version; a get_wall build
# only stores its result if no write landed mid-build (else it would pin a stale
# snapshot for the whole TTL right after the first unlock). All access under _lock.
_data_version = 0
_wall_cache: dict = {"ts": 0.0, "data": None, "ver": -1}

# Tiny, intentionally conservative profanity filter (obfuscation-grade, like the
# token). Substring match on a short denylist; replace with a real lib in prod.
_PROFANITY = {"shit", "fuck", "cunt", "nigger", "faggot", "bitch", "asshole"}


# ── Catalogue ────────────────────────────────────────────────────────────────

def _load_feats():
    try:
        data = json.loads((BASE_DIR / "feats.json").read_text(encoding="utf-8"))
        return data.get("feats", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


FEATS = _load_feats()
FEAT_BY_ID = {f["id"]: f for f in FEATS if f.get("id")}


# ── Storage (SQLite on a single instance's persistent disk) ──────────────────

def _conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _conn()
    try:
        # No IP column — by design. display_name is denormalized + refreshed on
        # every POST so a rename propagates everywhere.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS unlocks (
                player_hash    TEXT NOT NULL,
                achievement_id TEXT NOT NULL,
                display_name   TEXT NOT NULL,
                unlocked_at    TEXT,
                PRIMARY KEY (player_hash, achievement_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


@app.on_event("startup")
def _startup():
    init_db()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _clean_name(name: str) -> str:
    name = (name or "").strip()[:32]
    low = name.lower()
    for bad in _PROFANITY:
        if bad in low:
            return "(hidden)"
    return name or "Anonymous"


def _client_ip(request: Request) -> str:
    # Used ONLY for ephemeral in-memory rate limiting — never stored, never logged.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _rate_ok(player_hash: str, ip: str) -> bool:
    key = (player_hash, ip)
    now = time.time()
    with _lock:
        # Bound memory: when the table grows large, drop every bucket whose most
        # recent hit has aged out of the window (cheap amortized sweep).
        if len(_rate) > _RATE_GC_THRESHOLD:
            for k in [k for k, v in _rate.items() if not v or now - v[-1] >= RATE_WINDOW_S]:
                del _rate[k]
        hits = [t for t in _rate.get(key, []) if now - t < RATE_WINDOW_S]
        if len(hits) >= RATE_MAX:
            _rate[key] = hits
            return False
        hits.append(now)
        _rate[key] = hits
    return True


def _short(player_hash: str) -> str:
    return (player_hash or "")[:6]


def _invalidate_cache():
    # Bump the data version under the lock so any get_wall build that read the DB
    # before this write won't store its now-stale result (it checks the version
    # back). Also clear the cached snapshot.
    global _data_version
    with _lock:
        _data_version += 1
        _wall_cache["ts"] = 0.0
        _wall_cache["data"] = None


# ── Models ───────────────────────────────────────────────────────────────────

class UnlockIn(BaseModel):
    display_name:   str = Field(min_length=0, max_length=64)
    player_hash:    str = Field(min_length=4, max_length=128)
    achievement_id: str
    unlocked_at:    str | None = None


class RemoveIn(BaseModel):
    player_hash: str = Field(min_length=4, max_length=128)


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.post("/api/unlock")
def post_unlock(body: UnlockIn, request: Request,
                x_client_token: str | None = Header(default=None)):
    if x_client_token != CLIENT_TOKEN:
        return JSONResponse({"error": "bad client token"}, status_code=403)
    if body.achievement_id not in FEAT_BY_ID:
        # Reject anything not in the Feat catalogue — the wall is Feats-only.
        return JSONResponse({"error": "unknown achievement"}, status_code=400)
    if not _rate_ok(body.player_hash, _client_ip(request)):
        return JSONResponse({"error": "rate limited"}, status_code=429)
    name = _clean_name(body.display_name)
    conn = _conn()
    try:
        conn.execute(
            """
            INSERT INTO unlocks(player_hash, achievement_id, display_name, unlocked_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(player_hash, achievement_id) DO UPDATE SET
                display_name=excluded.display_name,
                unlocked_at=COALESCE(unlocks.unlocked_at, excluded.unlocked_at)
            """,
            (body.player_hash, body.achievement_id, name, body.unlocked_at),
        )
        conn.commit()
    finally:
        conn.close()
    _invalidate_cache()
    return {"ok": True}


@app.post("/api/remove")
def post_remove(body: RemoveIn, x_client_token: str | None = Header(default=None)):
    # Doubles as the takedown-by-hash moderation path. Succeeds on zero rows.
    # Idempotent + ordered so a still-queued unlock can't resurrect the hash:
    # the client stops re-sending after a removal; a late unlock would simply
    # re-add, which is why takedown is the authoritative server-side delete.
    if x_client_token != CLIENT_TOKEN:
        return JSONResponse({"error": "bad client token"}, status_code=403)
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM unlocks WHERE player_hash=?", (body.player_hash,))
        conn.commit()
        removed = cur.rowcount
    finally:
        conn.close()
    _invalidate_cache()
    return {"ok": True, "removed": max(0, removed)}


@app.get("/api/wall")
def get_wall():
    # Hidden-until-first-global-unlock: only Feats with >=1 unlocker appear.
    # Cold start returns 200 [] (never 500). Short-TTL cache over the scan.
    now = time.time()
    with _lock:
        if (_wall_cache["data"] is not None
                and now - _wall_cache["ts"] < WALL_CACHE_TTL_S
                and _wall_cache["ver"] == _data_version):
            return _wall_cache["data"]
        ver = _data_version          # snapshot the version this build is based on
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT achievement_id, display_name, player_hash FROM unlocks")
        rows = cur.fetchall()
    finally:
        conn.close()
    by_feat: dict[str, list] = {}
    for r in rows:
        by_feat.setdefault(r["achievement_id"], []).append(
            {"name": r["display_name"], "hash": _short(r["player_hash"])})
    out = []
    for fid, unlockers in by_feat.items():
        feat = FEAT_BY_ID.get(fid)
        if not feat:
            continue
        # `secret` Feats reveal their description only once globally unlocked —
        # which, by definition, they are here (>=1 unlocker), so show it.
        out.append({
            "id": fid,
            "title": feat.get("title", fid),
            "description": feat.get("description", ""),
            "secret": bool(feat.get("secret", False)),
            "count": len(unlockers),
            "unlockers": sorted(unlockers, key=lambda u: u["name"].lower()),
        })
    out.sort(key=lambda f: (-f["count"], f["title"].lower()))
    with _lock:
        # Only cache if no write landed while we were building — otherwise we'd
        # pin a stale snapshot (e.g. an empty wall right after the first unlock).
        if ver == _data_version:
            _wall_cache["ts"] = now
            _wall_cache["data"] = out
            _wall_cache["ver"] = ver
    return out


@app.get("/feats.json")
def get_feats():
    # Canonical catalogue. `secret` Feats hide their description until first
    # global unlock; the wall (above) reveals it once unlocked.
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT achievement_id FROM unlocks")
        unlocked_ids = {r["achievement_id"] for r in cur.fetchall()}
    finally:
        conn.close()
    out = []
    for f in FEATS:
        fid = f.get("id")
        if not fid:
            continue  # malformed catalogue entry — skip, never 500 the endpoint
        item = {"id": fid, "title": f.get("title", fid), "secret": bool(f.get("secret", False))}
        if not item["secret"] or fid in unlocked_ids:
            item["description"] = f.get("description", "")
        out.append(item)
    return {"version": 1, "feats": out}


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(str(BASE_DIR / "static" / "wall.html"))
