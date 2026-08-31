from __future__ import annotations

from app.security import totp_code


def test_admin_login_requires_and_accepts_totp(client):
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "Admin-Test-123!"},
    )
    assert login.status_code == 200, login.text
    payload = login.json()
    assert payload["requires_2fa"] is True
    assert payload["setup_required"] is True
    assert payload["secret"]

    verify = client.post(
        "/api/v1/auth/2fa/verify",
        headers={"X-Challenge-Token": payload["challenge_token"]},
        json={"code": totp_code(payload["secret"])},
    )
    assert verify.status_code == 200, verify.text
    assert verify.json()["role"] == "ADMIN"

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
