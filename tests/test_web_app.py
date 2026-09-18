import asyncio
import threading
from collections import defaultdict

import claude_agent_sdk as sdk
import pytest
from fastapi.testclient import TestClient

import agent
import auth
import db
import web_app

FAKE_USER = db.UserRecord(id=1, email="user@example.com", password_hash="x", created_at="")


@pytest.fixture
def authenticated():
    """Contourne auth.get_current_user : ces tests portent sur /api/chat, pas
    sur le flux d'authentification (voir tests/test_auth.py pour ça)."""
    web_app.app.dependency_overrides[auth.get_current_user] = lambda: FAKE_USER
    yield FAKE_USER
    web_app.app.dependency_overrides.pop(auth.get_current_user, None)


def make_fake_client(messages):
    class FakeClient:
        def __init__(self):
            self.queried_with = None

        async def query(self, message):
            self.queried_with = message

        async def receive_response(self):
            for m in messages:
                yield m

    return FakeClient()


def stub_client(monkeypatch, fake_client):
    async def fake_get_or_create_client(user_id):
        return fake_client

    monkeypatch.setattr(agent, "get_or_create_client", fake_get_or_create_client)


def test_build_agent_options_is_hardened():
    """Non-régression : sans tools=["WebSearch"] + strict_mcp_config=True +
    setting_sources=[], allowed_tools seul ne restreint rien — le CLI garde
    son jeu d'outils complet (Bash, Read, Write...) et charge les MCP/settings
    utilisateur (~/.claude/), voir CLAUDE.md § "Tool sandboxing footgun"."""
    options = agent.build_agent_options()
    assert options.tools == ["WebSearch"]
    assert options.strict_mcp_config is True
    assert options.setting_sources == []


async def test_get_or_create_client_isolates_users(monkeypatch):
    captured = []

    class FakeClaudeSDKClient:
        def __init__(self, options=None):
            captured.append(options)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClaudeSDKClient)
    monkeypatch.setattr(agent, "_clients", {})
    monkeypatch.setattr(agent, "_locks", defaultdict(asyncio.Lock))

    client_1a = await agent.get_or_create_client(1)
    client_1b = await agent.get_or_create_client(1)
    client_2 = await agent.get_or_create_client(2)

    assert client_1a is client_1b, "pas de reconnexion pour un utilisateur déjà connu"
    assert client_1a is not client_2, "deux utilisateurs doivent avoir des clients isolés"
    assert len(captured) == 2
    assert all(o.strict_mcp_config is True for o in captured)


def test_chat_connects_new_user_without_deadlocking(monkeypatch, authenticated):
    """Non-régression : get_or_create_client() ne doit pas ré-acquérir
    get_user_lock(user_id) en interne. web_app.chat() tient déjà ce verrou
    pour tout le tour ; asyncio.Lock n'étant pas réentrant, un double-acquire
    ici bloquait indéfiniment le tout premier message de chaque utilisateur."""

    class FakeClaudeSDKClient:
        def __init__(self, options=None):
            pass

        async def connect(self):
            pass

        async def query(self, message):
            pass

        async def receive_response(self):
            yield sdk.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="sess-1",
                total_cost_usd=0.0,
            )

        async def disconnect(self):
            pass

    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClaudeSDKClient)
    monkeypatch.setattr(agent, "_clients", {})
    monkeypatch.setattr(agent, "_locks", defaultdict(asyncio.Lock))

    client = TestClient(web_app.app)
    result = {}

    def run():
        result["resp"] = client.post("/api/chat", json={"message": "Bonjour"})

    # Thread daemon : si le deadlock revient, il reste bloqué pour toujours,
    # mais un thread daemon n'empêche pas le process pytest de se terminer
    # (contrairement à un ThreadPoolExecutor, dont __exit__ attendrait le
    # thread indéfiniment).
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive(), "chat() a deadlocké sur le premier message d'un nouvel utilisateur"
    resp = result["resp"]
    assert resp.status_code == 200
    assert "event: done" in resp.text


def test_chat_requires_authentication():
    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "Bonjour"})
    assert resp.status_code == 401


def test_chat_empty_message_returns_400(authenticated):
    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_chat_streams_text_and_done_events(monkeypatch, authenticated):
    fake_messages = [
        sdk.AssistantMessage(content=[sdk.TextBlock(text="Bonjour")], model="claude-test"),
        sdk.ResultMessage(
            subtype="success",
            duration_ms=123,
            duration_api_ms=100,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            total_cost_usd=0.01,
        ),
    ]
    stub_client(monkeypatch, make_fake_client(fake_messages))

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "Bonjour"})

    assert resp.status_code == 200
    body = resp.text
    assert "event: text" in body
    assert "data: Bonjour" in body
    assert "event: done" in body
    assert '"cost_usd": 0.01' in body


def test_chat_forwards_message_to_client(monkeypatch, authenticated):
    fake_client = make_fake_client([])
    stub_client(monkeypatch, fake_client)

    client = TestClient(web_app.app)
    client.post("/api/chat", json={"message": "  Quelle heure est-il ?  "})

    assert fake_client.queried_with == "Quelle heure est-il ?"


def test_chat_streams_error_event_on_exception(monkeypatch, authenticated):
    class FailingClient:
        async def query(self, message):
            raise RuntimeError("boom")

        async def receive_response(self):
            return
            yield  # pragma: no cover - jamais atteint, nécessaire pour un générateur async

    stub_client(monkeypatch, FailingClient())

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "test"})

    assert "event: error" in resp.text
    assert "boom" in resp.text
