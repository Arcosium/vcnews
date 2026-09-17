"""VCNews JWT 만료와 보안 쿠키 계약."""

import jwt
from fastapi import Response

from vcnews import app as app_module
from vcnews import auth


def test_regular_token_has_expiry():
    token = auth.issue_token(1, "user")
    payload = jwt.decode(
        token, auth.JWT_SECRET, algorithms=[auth.JWT_ALGORITHM],
    )
    assert "exp" in payload
    assert "persistent" not in payload


def test_secure_cookie_supports_cross_site_iframe(monkeypatch):
    monkeypatch.setattr(app_module, "_COOKIE_SECURE", True)
    response = Response()
    app_module._set_session_cookie(response, "token", remember=True)
    header = response.headers["set-cookie"]
    assert "HttpOnly" in header
    assert "SameSite=none" in header
    assert "Secure" in header
    assert "Partitioned" in header
