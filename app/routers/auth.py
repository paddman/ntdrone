from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import get_current_user
from app.models import Team, TeamStatus, User
from app.schemas import LoginRequest, TOTPVerifyRequest, UserSummary
from app.security import (
    encrypt_secret,
    generate_totp_secret,
    get_token_service,
    provisioning_uri,
    qr_data_uri,
    verify_password,
    verify_totp,
)
from app.services import audit

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


@router.post("/login")
def login(
    payload: LoginRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    user = db.scalar(select(User).where(func.lower(User.username) == payload.username.strip().lower()))
    if not user or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Username หรือ Password ไม่ถูกต้อง")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="บัญชียังไม่ได้รับอนุมัติหรือถูกปิดใช้งาน")
    if user.team_id:
        team = db.get(Team, user.team_id)
        if not team or team.status != TeamStatus.APPROVED.value:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ทีมยังไม่ได้รับอนุมัติ")

    setup_required = not user.totp_enabled
    secret: str | None = None
    if setup_required:
        if user.totp_secret_encrypted:
            from app.security import decrypt_secret

            secret = decrypt_secret(user.totp_secret_encrypted, settings)
        else:
            secret = generate_totp_secret()
            user.totp_secret_encrypted = encrypt_secret(secret, settings)
            db.commit()
    challenge = get_token_service(settings).create_challenge(user.id)
    response: dict[str, object] = {
        "challenge_token": challenge,
        "requires_2fa": True,
        "setup_required": setup_required,
    }
    if secret:
        uri = provisioning_uri(secret, user.email)
        response.update({"secret": secret, "provisioning_uri": uri, "qr_data_uri": qr_data_uri(uri)})
    return response


@router.post("/2fa/verify", response_model=UserSummary)
def verify_two_factor(
    payload: TOTPVerifyRequest,
    response: Response,
    x_challenge_token: str = Header(alias="X-Challenge-Token"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    token_data = get_token_service(settings).read_challenge(x_challenge_token)
    if not token_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="2FA challenge หมดอายุ")
    user = db.get(User, token_data.get("sub"))
    if not user or not user.is_active or not user.totp_secret_encrypted:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="บัญชีหรือ 2FA challenge ไม่ถูกต้อง")

    from app.security import decrypt_secret

    secret = decrypt_secret(user.totp_secret_encrypted, settings)
    if not verify_totp(secret, payload.code):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="รหัส 2FA ไม่ถูกต้อง")
    user.totp_enabled = True
    user.last_login_at = datetime.now(timezone.utc)
    audit(db, user.id, "LOGIN_2FA_SUCCESS", "user", user.id)
    db.commit()
    session_token = get_token_service(settings).create_session(user.id, user.role)
    response.set_cookie(
        settings.session_cookie_name,
        session_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return user


@router.post("/logout", status_code=204)
def logout(response: Response, settings: Settings = Depends(get_settings)) -> Response:
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.get("/me", response_model=UserSummary)
def me(user: User = Depends(get_current_user)) -> User:
    return user
