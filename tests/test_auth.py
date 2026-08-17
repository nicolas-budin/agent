from fastapi.testclient import TestClient

import auth
import db
import web_app


def test_register_creates_user_and_sets_session_cookie():
    client = TestClient(web_app.app)
    resp = client.post("/api/register", json={"email": "new@example.com", "password": "hunter22"})

    assert resp.status_code == 201
    assert resp.json() == {"id": 1, "email": "new@example.com"}
    assert auth.SESSION_COOKIE_NAME in resp.cookies

    stored = db.get_user_by_email("new@example.com")
    assert stored is not None
    assert stored.password_hash != "hunter22"  # jamais en clair


def test_register_normalizes_email():
    client = TestClient(web_app.app)
    client.post("/api/register", json={"email": "  Mixed@Example.com  ", "password": "hunter22"})

    assert db.get_user_by_email("mixed@example.com") is not None


def test_register_rejects_short_password():
    client = TestClient(web_app.app)
    resp = client.post("/api/register", json={"email": "short@example.com", "password": "abc"})

    assert resp.status_code == 400
    assert db.get_user_by_email("short@example.com") is None


def test_register_duplicate_email_returns_409():
    client = TestClient(web_app.app)
    client.post("/api/register", json={"email": "dup@example.com", "password": "hunter22"})
    resp = client.post("/api/register", json={"email": "dup@example.com", "password": "otherpass"})

    assert resp.status_code == 409


def test_login_unknown_email_returns_401():
    client = TestClient(web_app.app)
    resp = client.post("/api/login", json={"email": "ghost@example.com", "password": "whatever"})
    assert resp.status_code == 401


def test_login_wrong_password_returns_401():
    client = TestClient(web_app.app)
    client.post("/api/register", json={"email": "user@example.com", "password": "correcthorse"})
    resp = client.post("/api/login", json={"email": "user@example.com", "password": "wrongpass"})
    assert resp.status_code == 401


def test_login_success_sets_cookie_and_returns_user():
    register_client = TestClient(web_app.app)
    register_client.post("/api/register", json={"email": "user2@example.com", "password": "correcthorse"})

    login_client = TestClient(web_app.app)
    resp = login_client.post("/api/login", json={"email": "user2@example.com", "password": "correcthorse"})

    assert resp.status_code == 200
    assert resp.json()["email"] == "user2@example.com"
    assert auth.SESSION_COOKIE_NAME in resp.cookies


def test_me_returns_401_when_not_authenticated():
    client = TestClient(web_app.app)
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_me_returns_user_when_authenticated():
    # Un seul TestClient pour toute la séquence : httpx conserve le cookie de
    # session reçu au register pour les appels suivants sur ce même client.
    client = TestClient(web_app.app)
    client.post("/api/register", json={"email": "me@example.com", "password": "correcthorse"})

    resp = client.get("/api/me")

    assert resp.status_code == 200
    assert resp.json() == {"id": resp.json()["id"], "email": "me@example.com"}


def test_chat_returns_401_without_session():
    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "Bonjour"})
    assert resp.status_code == 401


def test_logout_clears_session():
    client = TestClient(web_app.app)
    client.post("/api/register", json={"email": "logout@example.com", "password": "correcthorse"})
    assert client.get("/api/me").status_code == 200

    logout_resp = client.post("/api/logout")
    assert logout_resp.status_code == 200

    assert client.get("/api/me").status_code == 401
