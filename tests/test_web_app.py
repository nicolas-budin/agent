import asyncio
from collections import defaultdict
from types import SimpleNamespace

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
    assert "mcp__docs__search_docs" in options.allowed_tools


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
    assert "event: sources" not in body  # search_docs pas appelé dans ce tour


def test_chat_streams_sources_event_when_search_docs_was_used(monkeypatch, authenticated):
    fake_messages = [
        sdk.AssistantMessage(content=[sdk.TextBlock(text="Réponse")], model="claude-test"),
        sdk.ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            total_cost_usd=0.0,
        ),
    ]

    class FakeClientWithTool:
        async def query(self, message):
            # simule search_docs ayant été appelé (et donc current_sources_var
            # rempli) pendant ce tour, avant que la ResultMessage n'arrive.
            agent.current_sources_var.get().extend(["web_app.py", "index_docs.py"])

        async def receive_response(self):
            for m in fake_messages:
                yield m

    stub_client(monkeypatch, FakeClientWithTool())

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "Comment fonctionne le RAG ?"})

    assert "event: sources" in resp.text
    assert '["web_app.py", "index_docs.py"]' in resp.text


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


async def test_search_docs_returns_formatted_chunks(monkeypatch):
    agent.current_sources_var.set([])
    fake_points = [
        SimpleNamespace(payload={"source": "web_app.py", "text": "contenu du chunk 1"}),
        SimpleNamespace(payload={"source": "index_docs.py", "text": "contenu du chunk 2"}),
    ]
    monkeypatch.setattr(
        agent.qdrant, "query_points", lambda **kwargs: SimpleNamespace(points=fake_points)
    )

    result = await agent.search_docs.handler({"query": "test"})

    text = result["content"][0]["text"]
    assert "[web_app.py]" in text
    assert "contenu du chunk 1" in text
    assert "[index_docs.py]" in text
    assert "contenu du chunk 2" in text


async def test_search_docs_handles_no_results(monkeypatch):
    monkeypatch.setattr(
        agent.qdrant, "query_points", lambda **kwargs: SimpleNamespace(points=[])
    )

    result = await agent.search_docs.handler({"query": "rien à voir"})

    assert result["content"][0]["text"] == "Aucun résultat trouvé."


async def test_search_docs_records_sources_without_duplicates(monkeypatch):
    agent.current_sources_var.set([])
    fake_points = [
        SimpleNamespace(payload={"source": "web_app.py", "text": "chunk A"}),
        SimpleNamespace(payload={"source": "web_app.py", "text": "chunk B"}),
        SimpleNamespace(payload={"source": "index_docs.py", "text": "chunk C"}),
    ]
    monkeypatch.setattr(
        agent.qdrant, "query_points", lambda **kwargs: SimpleNamespace(points=fake_points)
    )

    await agent.search_docs.handler({"query": "test"})

    assert agent.current_sources_var.get() == ["web_app.py", "index_docs.py"]
