from types import SimpleNamespace

import claude_agent_sdk as sdk
from fastapi.testclient import TestClient

import web_app

# TestClient(web_app.app) sans `with` : le lifespan (connexion réelle au CLI
# Claude) ne se déclenche pas — on mocke `web_app.client` à la place.


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


def test_lifespan_configures_secure_agent_options(monkeypatch):
    """Non-régression : sans tools=["WebSearch"] + strict_mcp_config=True +
    setting_sources=[], allowed_tools seul ne restreint rien — le CLI garde
    son jeu d'outils complet (Bash, Read, Write...) et charge les MCP/settings
    utilisateur (~/.claude/), voir CLAUDE.md § "Tool sandboxing footgun"."""
    captured = {}

    class FakeClaudeSDKClient:
        def __init__(self, options=None):
            captured["options"] = options

        async def connect(self):
            pass

        async def disconnect(self):
            pass

    monkeypatch.setattr(web_app, "ClaudeSDKClient", FakeClaudeSDKClient)

    with TestClient(web_app.app):
        pass  # le lifespan s'exécute à l'entrée/sortie du bloc `with`

    options = captured["options"]
    assert options is not None, "ClaudeSDKClient n'a jamais été instancié"
    assert options.tools == ["WebSearch"]
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    assert "mcp__docs__search_docs" in options.allowed_tools


def test_chat_empty_message_returns_400():
    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "   "})
    assert resp.status_code == 400


def test_chat_streams_text_and_done_events(monkeypatch):
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
    monkeypatch.setattr(web_app, "client", make_fake_client(fake_messages))

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "Bonjour"})

    assert resp.status_code == 200
    body = resp.text
    assert "event: text" in body
    assert "data: Bonjour" in body
    assert "event: done" in body
    assert '"cost_usd": 0.01' in body
    assert "event: sources" not in body  # search_docs pas appelé dans ce tour


def test_chat_streams_sources_event_when_search_docs_was_used(monkeypatch):
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
            # simule search_docs ayant été appelé (et donc current_sources rempli)
            # pendant ce tour, avant que la ResultMessage n'arrive.
            web_app.current_sources.extend(["web_app.py", "index_docs.py"])

        async def receive_response(self):
            for m in fake_messages:
                yield m

    monkeypatch.setattr(web_app, "client", FakeClientWithTool())

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "Comment fonctionne le RAG ?"})

    assert "event: sources" in resp.text
    assert '["web_app.py", "index_docs.py"]' in resp.text


def test_chat_forwards_message_to_client(monkeypatch):
    fake_client = make_fake_client([])
    monkeypatch.setattr(web_app, "client", fake_client)

    client = TestClient(web_app.app)
    client.post("/api/chat", json={"message": "  Quelle heure est-il ?  "})

    assert fake_client.queried_with == "Quelle heure est-il ?"


def test_chat_streams_error_event_on_exception(monkeypatch):
    class FailingClient:
        async def query(self, message):
            raise RuntimeError("boom")

        async def receive_response(self):
            return
            yield  # pragma: no cover - jamais atteint, nécessaire pour un générateur async

    monkeypatch.setattr(web_app, "client", FailingClient())

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "test"})

    assert "event: error" in resp.text
    assert "boom" in resp.text


async def test_search_docs_returns_formatted_chunks(monkeypatch):
    web_app.current_sources.clear()
    fake_points = [
        SimpleNamespace(payload={"source": "web_app.py", "text": "contenu du chunk 1"}),
        SimpleNamespace(payload={"source": "index_docs.py", "text": "contenu du chunk 2"}),
    ]
    monkeypatch.setattr(
        web_app.qdrant, "query_points", lambda **kwargs: SimpleNamespace(points=fake_points)
    )

    result = await web_app.search_docs.handler({"query": "test"})

    text = result["content"][0]["text"]
    assert "[web_app.py]" in text
    assert "contenu du chunk 1" in text
    assert "[index_docs.py]" in text
    assert "contenu du chunk 2" in text


async def test_search_docs_handles_no_results(monkeypatch):
    monkeypatch.setattr(
        web_app.qdrant, "query_points", lambda **kwargs: SimpleNamespace(points=[])
    )

    result = await web_app.search_docs.handler({"query": "rien à voir"})

    assert result["content"][0]["text"] == "Aucun résultat trouvé."


async def test_search_docs_records_sources_without_duplicates(monkeypatch):
    web_app.current_sources.clear()
    fake_points = [
        SimpleNamespace(payload={"source": "web_app.py", "text": "chunk A"}),
        SimpleNamespace(payload={"source": "web_app.py", "text": "chunk B"}),
        SimpleNamespace(payload={"source": "index_docs.py", "text": "chunk C"}),
    ]
    monkeypatch.setattr(
        web_app.qdrant, "query_points", lambda **kwargs: SimpleNamespace(points=fake_points)
    )

    await web_app.search_docs.handler({"query": "test"})

    assert web_app.current_sources == ["web_app.py", "index_docs.py"]
