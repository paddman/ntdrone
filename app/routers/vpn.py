from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import get_current_user
from app.models import Team, TeamStatus, User, VPNCredential
from app.services import render_wireguard_config

router = APIRouter(prefix="/api/v1/vpn", tags=["VPN"])


@router.get("/status")
def vpn_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    if not user.team_id:
        raise HTTPException(status_code=404, detail="Admin ไม่มี VPN รายทีม")
    credential = db.scalar(select(VPNCredential).where(VPNCredential.team_id == user.team_id))
    if not credential:
        return {"provisioned": False, "enabled": False}
    return {
        "provisioned": True,
        "enabled": credential.enabled,
        "address": credential.address,
        "last_enabled_at": credential.last_enabled_at,
        "last_disabled_at": credential.last_disabled_at,
    }


@router.get("/config")
def download_vpn_config(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not user.team_id:
        raise HTTPException(status_code=404, detail="Admin ไม่มี VPN config รายทีม")
    team = db.get(Team, user.team_id)
    if not team or team.status != TeamStatus.APPROVED.value:
        raise HTTPException(status_code=403, detail="ทีมยังไม่ได้รับอนุมัติ")
    credential = db.scalar(select(VPNCredential).where(VPNCredential.team_id == user.team_id))
    if not credential:
        raise HTTPException(status_code=404, detail="ยังไม่ได้สร้าง VPN credential")
    content = render_wireguard_config(credential, settings)
    return Response(
        content=content,
        media_type="text/plain",
        headers={"Content-Disposition": f'attachment; filename="{team.code}.conf"'},
    )
