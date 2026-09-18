import threading

import claude_agent_sdk as sdk
from fastapi.testclient import TestClient

import agent
import web_app


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
    async def fake_get_or_create_client():
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


async def test_get_or_create_client_reuses_the_same_client(monkeypatch):
    captured = []

    class FakeClaudeSDKClient:
        def __init__(self, options=None):
            captured.append(options)

        async def connect(self):
            pass

        async def disconnect(self):
            pass

    monkeypatch.setattr(agent, "ClaudeSDKClient", FakeClaudeSDKClient)
    monkeypatch.setattr(agent, "_client", None)

    client_a = await agent.get_or_create_client()
    client_b = await agent.get_or_create_client()

    assert client_a is client_b, "pas de reconnexion une fois le client déjà créé"
    assert len(captured) == 1
    assert captured[0].strict_mcp_config is True


def test_chat_connects_without_deadlocking(monkeypatch):
    """Non-régression : get_or_create_client() ne doit pas ré-acquérir
    agent.get_lock() en interne. web_app.chat() tient déjà ce verrou pour
    tout le tour ; asyncio.Lock n'étant pas réentrant, un double-acquire ici
    bloquait indéfiniment le tout premier message reçu par le serveur."""

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
    monkeypatch.setattr(agent, "_client", None)

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

    assert not thread.is_alive(), "chat() a deadlocké sur le premier message"
    resp = result["resp"]
    assert resp.status_code == 200
    assert "event: done" in resp.text


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
    stub_client(monkeypatch, make_fake_client(fake_messages))

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "Bonjour"})

    assert resp.status_code == 200
    body = resp.text
    assert "event: text" in body
    assert "data: Bonjour" in body
    assert "event: done" in body
    assert '"cost_usd": 0.01' in body


def test_chat_forwards_message_to_client(monkeypatch):
    fake_client = make_fake_client([])
    stub_client(monkeypatch, fake_client)

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

    stub_client(monkeypatch, FailingClient())

    client = TestClient(web_app.app)
    resp = client.post("/api/chat", json={"message": "test"})

    assert "event: error" in resp.text
    assert "boom" in resp.text
