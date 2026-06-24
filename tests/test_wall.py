"""Hosted Feats wall — reveal logic, removal, validation, no-IP, cold start."""

import sqlite3

from conftest import TOKEN

UNLOCK = {"display_name": "Ada", "player_hash": "deadbeefcafe1234",
          "achievement_id": "notes_total", "unlocked_at": "2026-06-24T00:00:00Z"}


def test_cold_start_wall_is_200_empty(client):
    r = client.get("/api/wall")
    assert r.status_code == 200
    assert r.json() == []


def test_reveal_hidden_until_first_unlock(client):
    # 0 global unlocks of a feat → omitted; after 1 → visible with unlocker list.
    assert client.get("/api/wall").json() == []
    assert client.post("/api/unlock", json=UNLOCK, headers=TOKEN).status_code == 200
    wall = client.get("/api/wall").json()
    assert len(wall) == 1
    entry = wall[0]
    assert entry["id"] == "notes_total"
    assert entry["count"] == 1
    assert entry["unlockers"][0]["name"] == "Ada"
    assert entry["unlockers"][0]["hash"] == "deadbe"  # short suffix only


def test_unknown_achievement_rejected(client):
    bad = dict(UNLOCK, achievement_id="totally_not_a_feat")
    r = client.post("/api/unlock", json=bad, headers=TOKEN)
    assert r.status_code == 400


def test_competency_id_rejected(client):
    # A competency id must never be accepted — the wall is Feats-only.
    r = client.post("/api/unlock", json=dict(UNLOCK, achievement_id="tempo_push"), headers=TOKEN)
    assert r.status_code == 400


def test_bad_token_forbidden(client):
    assert client.post("/api/unlock", json=UNLOCK).status_code == 403
    assert client.post("/api/unlock", json=UNLOCK, headers={"X-Client-Token": "nope"}).status_code == 403


def test_remove_succeeds_on_zero_rows(client):
    r = client.post("/api/remove", json={"player_hash": "nobody-here"}, headers=TOKEN)
    assert r.status_code == 200
    assert r.json()["removed"] == 0


def test_remove_deletes_all_rows_for_hash(client):
    client.post("/api/unlock", json=UNLOCK, headers=TOKEN)
    client.post("/api/unlock", json=dict(UNLOCK, achievement_id="songs_done"), headers=TOKEN)
    r = client.post("/api/remove", json={"player_hash": UNLOCK["player_hash"]}, headers=TOKEN)
    assert r.json()["removed"] == 2
    assert client.get("/api/wall").json() == []


def test_no_ip_column_anywhere(client):
    client.post("/api/unlock", json=UNLOCK, headers=TOKEN)
    db = sqlite3.connect(str(client._server.DB_PATH))
    cols = [c[1] for c in db.execute("PRAGMA table_info(unlocks)")]
    db.close()
    assert "ip" not in [c.lower() for c in cols]
    assert set(cols) == {"player_hash", "achievement_id", "display_name", "unlocked_at"}


def test_display_name_refreshes_on_repost(client):
    client.post("/api/unlock", json=UNLOCK, headers=TOKEN)
    client.post("/api/unlock", json=dict(UNLOCK, display_name="Ada Lovelace"), headers=TOKEN)
    wall = client.get("/api/wall").json()
    assert wall[0]["unlockers"][0]["name"] == "Ada Lovelace"
    assert wall[0]["count"] == 1  # still one row (upsert, not duplicate)


def test_profanity_filtered(client):
    client.post("/api/unlock", json=dict(UNLOCK, display_name="fuckface"), headers=TOKEN)
    assert client.get("/api/wall").json()[0]["unlockers"][0]["name"] == "(hidden)"


def test_secret_feat_description_hidden_until_unlocked(client):
    feats = {f["id"]: f for f in client.get("/feats.json").json()["feats"]}
    # secret_combo ships secret → description withheld before any unlock.
    assert feats["secret_combo"]["secret"] is True
    assert "description" not in feats["secret_combo"]
    # Unlock it → description now revealed.
    client.post("/api/unlock", json=dict(UNLOCK, achievement_id="secret_combo"), headers=TOKEN)
    feats2 = {f["id"]: f for f in client.get("/feats.json").json()["feats"]}
    assert "description" in feats2["secret_combo"]


def test_rate_limit_kicks_in(client):
    client._server.RATE_MAX = 3
    ok = sum(client.post("/api/unlock", json=dict(UNLOCK, achievement_id="songs_done"),
                         headers=TOKEN).status_code == 200 for _ in range(3))
    assert ok == 3
    assert client.post("/api/unlock", json=dict(UNLOCK, achievement_id="time_total"),
                       headers=TOKEN).status_code == 429
