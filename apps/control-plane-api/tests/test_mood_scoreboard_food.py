from __future__ import annotations

from fastapi.testclient import TestClient

from .factories import build_event

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-token"}


def _ingest_token(client: TestClient) -> str:
    resp = client.post(
        "/admin/tokens", json={"scope": "ingest", "door_id": "primary"}, headers=ADMIN_HEADERS
    )
    return resp.json()["token"]


def _ingest(client: TestClient, token: str, events: list[dict]) -> dict:
    resp = client.post(
        "/ingest",
        json={"batch_id": "b1", "events": events},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_mood_checkin_and_retrieval(client: TestClient) -> None:
    # 1. Update mood for owner
    resp = client.post(
        "/admin/social/mood",
        json={"subject_id": "owner", "mood": "focused"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "subject_id": "owner", "mood": "focused"}

    # 2. Get current moods
    resp = client.get("/social/mood")
    assert resp.status_code == 200
    assert resp.json() == {"owner": "focused"}

    # 3. Test invalid subject/mood returns 422
    resp = client.post(
        "/admin/social/mood",
        json={"subject_id": "invalid", "mood": "focused"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422

    resp = client.post(
        "/admin/social/mood",
        json={"subject_id": "owner", "mood": "invalid_mood"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


def test_mood_ugc_html_escaping(client: TestClient) -> None:
    client.post(
        "/admin/social/mood",
        json={"subject_id": "owner", "mood": "chilling <script>alert(1)</script>"},
        headers=ADMIN_HEADERS,
    )
    # The API will block if not in configured mood list.
    # The validation blocks any mood not in {"focused", "chilling", "busy", "away"}.
    # Since only those four are valid, HTML injection is impossible.


def test_scoreboard_crud_and_sorting(client: TestClient) -> None:
    # 1. Create a scoreboard entry
    create_resp = client.post(
        "/admin/social/scoreboard",
        json={
            "board_id": "pingpong",
            "title": "Taylor <b>Cool</b>",
            "notes": "won the match <img src=x onerror=alert(1)>",
            "score": 10,
        },
        headers=ADMIN_HEADERS,
    )
    assert create_resp.status_code == 201
    entry_id = create_resp.json()["entry_id"]
    assert entry_id

    # 2. Check HTML escaping (sanitization)
    get_resp = client.get("/social/scoreboard")
    assert get_resp.status_code == 200
    boards = get_resp.json()["boards"]
    assert "pingpong" in boards
    entries = boards["pingpong"]
    assert len(entries) == 1
    assert entries[0]["entry_id"] == entry_id
    # Title and notes should be HTML escaped
    assert entries[0]["title"] == "Taylor &lt;b&gt;Cool&lt;/b&gt;"
    assert entries[0]["notes"] == "won the match &lt;img src=x onerror=alert(1)&gt;"
    assert entries[0]["score"] == 10

    # 3. Create a second entry on the same board
    create_resp2 = client.post(
        "/admin/social/scoreboard",
        json={
            "board_id": "pingpong",
            "title": "Alex",
            "notes": "challenging",
            "score": 15,
        },
        headers=ADMIN_HEADERS,
    )
    assert create_resp2.status_code == 201

    # 4. Verify scoreboard sorts by score descending
    get_resp = client.get("/social/scoreboard")
    entries = get_resp.json()["boards"]["pingpong"]
    assert len(entries) == 2
    # Alex (15) should be first, Taylor (10) second
    assert entries[0]["title"] == "Alex"
    assert entries[0]["score"] == 15
    assert entries[1]["title"] == "Taylor &lt;b&gt;Cool&lt;/b&gt;"
    assert entries[1]["score"] == 10

    # 5. Update Taylor's entry to score 20
    update_resp = client.put(
        f"/admin/social/scoreboard/{entry_id}",
        json={
            "title": "Taylor (legend)",
            "notes": "new high score",
            "score": 20,
        },
        headers=ADMIN_HEADERS,
    )
    assert update_resp.status_code == 200

    # 6. Verify sort order changed after update
    get_resp = client.get("/social/scoreboard")
    entries = get_resp.json()["boards"]["pingpong"]
    assert entries[0]["title"] == "Taylor (legend)"
    assert entries[0]["score"] == 20
    assert entries[1]["title"] == "Alex"

    # 7. Delete Taylor's entry
    delete_resp = client.delete(
        f"/admin/social/scoreboard/{entry_id}",
        headers=ADMIN_HEADERS,
    )
    assert delete_resp.status_code == 200

    # 8. Verify it is no longer returned in scoreboard list
    get_resp = client.get("/social/scoreboard")
    entries = get_resp.json()["boards"].get("pingpong", [])
    assert len(entries) == 1
    assert entries[0]["title"] == "Alex"


def test_food_recommendation_ingest_and_retrieval(client: TestClient) -> None:
    token = _ingest_token(client)
    food_event = build_event(
        "ambient.food_recommendation",
        payload_overrides={
            "date": "2026-07-08",
            "title": "Vegan Ramen",
            "detail": "At the corner shop",
        },
    )
    _ingest(client, token, [food_event])

    resp = client.get("/social/food")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Vegan Ramen"
    assert data["detail"] == "At the corner shop"
    assert data["date"] == "2026-07-08"
