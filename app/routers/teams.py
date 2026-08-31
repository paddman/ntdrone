from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_current_user, require_roles
from app.models import Team, User, UserRole
from app.schemas import FeedbackCreate, MemberCreate, TeamSummary, UserSummary
from app.services import BusinessRuleError, create_feedback, create_team_member

router = APIRouter(prefix="/api/v1/teams", tags=["Teams"])


@router.get("/current", response_model=TeamSummary)
def current_team(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Team:
    if not user.team_id:
        raise HTTPException(status_code=404, detail="Admin ไม่มี Team profile")
    team = db.get(Team, user.team_id)
    if not team:
        raise HTTPException(status_code=404, detail="ไม่พบทีม")
    return team


@router.get("/current/members", response_model=list[UserSummary])
def current_members(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[User]:
    if not user.team_id:
        return []
    return list(db.scalars(select(User).where(User.team_id == user.team_id).order_by(User.created_at)).all())


@router.post("/current/members", response_model=UserSummary, status_code=201)
def add_member(
    payload: MemberCreate,
    leader: User = Depends(require_roles(UserRole.TEAM_LEADER)),
    db: Session = Depends(get_db),
) -> User:
    try:
        return create_team_member(db, leader, **payload.model_dump())
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/current/feedback", status_code=201)
def submit_feedback(
    payload: FeedbackCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    try:
        row = create_feedback(db, user, payload.rating, payload.message)
    except BusinessRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": row.id, "status": "received"}
