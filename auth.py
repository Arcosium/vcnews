"""VC News — 인증 유틸.

- 비밀번호: PBKDF2-HMAC-SHA256 (600k iter, 16-byte salt) — Python 내장만 사용.
- 세션: JWT (HS256) in HttpOnly 쿠키. 만료 30일.
- FastAPI dependency `current_user` 로 보호 엔드포인트에서 유저 로드.

JWT 시크릿은 `VCNEWS_JWT_SECRET` 환경변수에 두는 게 정석이지만,
없으면 영속 파일(`.jwt_secret`)에 자동 생성해 부팅마다 동일하게 유지.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import datetime
from typing import Optional

import jwt
from fastapi import Cookie, Depends, HTTPException

from .models import SessionLocal, User

# ─── 비밀번호 해시 ───────────────────────────────────────────

_PBKDF2_ITER = 600_000
_PBKDF2_ALGO = "sha256"
_PBKDF2_SALT_LEN = 16


def hash_password(password: str) -> str:
    """salt + iter + hash → 'pbkdf2_sha256$<iter>$<salt_hex>$<hash_hex>' 포맷."""
    if not password or len(password) < 4:
        raise ValueError("비밀번호는 4자 이상이어야 합니다")
    salt = secrets.token_bytes(_PBKDF2_SALT_LEN)
    dk = hashlib.pbkdf2_hmac(
        _PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ITER,
    )
    return f"pbkdf2_{_PBKDF2_ALGO}${_PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iter_s, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    if not scheme.startswith("pbkdf2_"):
        return False
    algo = scheme.split("_", 1)[1]
    try:
        iters = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(actual, expected)


# ─── JWT ────────────────────────────────────────────────────

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_SECRET_FILE = os.path.join(_THIS_DIR, ".jwt_secret")


def _load_or_create_secret() -> str:
    env = os.environ.get("VCNEWS_JWT_SECRET")
    if env:
        return env
    if os.path.exists(_SECRET_FILE):
        with open(_SECRET_FILE) as f:
            return f.read().strip()
    sec = secrets.token_urlsafe(48)
    with open(_SECRET_FILE, "w") as f:
        f.write(sec)
    os.chmod(_SECRET_FILE, 0o600)
    return sec


JWT_SECRET = _load_or_create_secret()
JWT_ALGORITHM = "HS256"
JWT_COOKIE_NAME = "vcnews_session"
JWT_EXPIRE_DAYS = 30


def issue_token(user_id: int, username: str) -> str:
    now = datetime.datetime.utcnow()
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(days=JWT_EXPIRE_DAYS)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        return None


# ─── FastAPI Dependency ────────────────────────────────────


def get_current_user(
    vcnews_session: Optional[str] = Cookie(default=None, alias=JWT_COOKIE_NAME),
) -> User:
    if not vcnews_session:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다")
    payload = decode_token(vcnews_session)
    if not payload:
        raise HTTPException(status_code=401, detail="세션이 만료되었습니다")
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="유효하지 않은 세션")
    session = SessionLocal()
    try:
        user = session.query(User).get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="유저를 찾을 수 없습니다")
        # detached: 세션 닫히기 전에 필요한 필드 access 해두기
        _ = (user.username, user.is_admin)
        session.expunge(user)
        return user
    finally:
        session.close()


def get_optional_user(
    vcnews_session: Optional[str] = Cookie(default=None, alias=JWT_COOKIE_NAME),
) -> Optional[User]:
    if not vcnews_session:
        return None
    try:
        return get_current_user(vcnews_session)
    except HTTPException:
        return None


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다")
    return user
