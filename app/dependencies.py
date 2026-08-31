from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import User, UserRole
from app.security import get_token_service


def get_session_token(
    request: Request,
    settings: Settings = Depends(get_settings),
    authorization: str | None = Header(default=None),
) -> str | None:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.cookies.get(settings.session_cookie_name)


def get_current_user(
    token: str | None = Depends(get_session_token),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="กรุณาเข้าสู่ระบบ")
    payload = get_token_service(settings).read_session(token)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session หมดอายุหรือไม่ถูกต้อง")
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="บัญชีถูกปิดใช้งาน")
    return user


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    payload = get_token_service(settings).read_session(token)
    if not payload:
        return None
    user = db.get(User, payload.get("sub"))
    if not user or not user.is_active:
        return None
    return user


def require_roles(*roles: UserRole | str) -> Callable[[User], User]:
    allowed = {role.value if isinstance(role, UserRole) else role for role in roles}

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ไม่มีสิทธิ์ใช้งานส่วนนี้")
        return user

    return dependency


def require_simulator_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = f"Bearer {settings.simulator_api_token}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid simulator bearer token")
