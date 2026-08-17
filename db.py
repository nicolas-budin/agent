import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Relu à chaque appel (jamais mis en cache au niveau module) pour rester
# monkeypatchable par les tests (voir tests/conftest.py).
DB_PATH = str(Path(__file__).parent / "users.db")


class EmailAlreadyRegisteredError(Exception):
    pass


@dataclass(frozen=True)
class UserRecord:
    id: int
    email: str
    password_hash: str
    created_at: str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            )
            """
        )


def _row_to_user(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=row["created_at"],
    )


def create_user(email: str, password_hash: str) -> UserRecord:
    created_at = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, password_hash, created_at),
            )
        except sqlite3.IntegrityError as exc:
            raise EmailAlreadyRegisteredError(email) from exc
        return UserRecord(
            id=cursor.lastrowid,
            email=email,
            password_hash=password_hash,
            created_at=created_at,
        )


def get_user_by_email(email: str) -> UserRecord | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return _row_to_user(row) if row else None


def get_user_by_id(user_id: int) -> UserRecord | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None
