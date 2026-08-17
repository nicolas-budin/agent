import secrets

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

import db

SESSION_COOKIE_NAME = "session_token"
MIN_PASSWORD_LENGTH = 8

# Démo mono-processus : token -> user_id, en mémoire (pas de scaling
# horizontal, pas de persistance des sessions entre redémarrages du serveur).
_sessions: dict[str, int] = {}


class Credentials(BaseModel):
    email: str
    password: str


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = user_id
    return token


def destroy_session(token: str | None) -> None:
    if token is not None:
        _sessions.pop(token, None)


def _set_session_cookie(response: Response, token: str) -> None:
    # secure=False car cette démo tourne en HTTP local — passer à True
    # derrière HTTPS en prod.
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


async def get_current_user(request: Request) -> db.UserRecord:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user_id = _sessions.get(token) if token else None
    if user_id is None:
        raise HTTPException(status_code=401, detail="Non authentifié")
    user = db.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Non authentifié")
    return user


router = APIRouter()


@router.post("/api/register", status_code=201)
async def register(body: Credentials, response: Response):
    email = _normalize_email(body.email)
    if len(body.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Le mot de passe doit contenir au moins {MIN_PASSWORD_LENGTH} caractères",
        )
    try:
        user = db.create_user(email, _hash_password(body.password))
    except db.EmailAlreadyRegisteredError:
        raise HTTPException(status_code=409, detail="Email déjà utilisé") from None

    token = create_session(user.id)
    _set_session_cookie(response, token)
    return {"id": user.id, "email": user.email}


@router.post("/api/login")
async def login(body: Credentials, response: Response):
    email = _normalize_email(body.email)
    user = db.get_user_by_email(email)
    if user is None or not _verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Identifiants invalides")

    token = create_session(user.id)
    _set_session_cookie(response, token)
    return {"id": user.id, "email": user.email}


@router.post("/api/logout")
async def logout(request: Request, response: Response):
    destroy_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/api/me")
async def me(user: db.UserRecord = Depends(get_current_user)):
    return {"id": user.id, "email": user.email}
