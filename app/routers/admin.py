from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.dependencies import require_roles
from app.models import Booking, BookingStatus, Slot, Team, User, UserRole
from app.schemas import BookingSummary, QueueAdjust, SlotCreate, SlotSummary, TeamDecision, TeamSummary
from app.services import BusinessRuleError, audit, create_slot, decide_team

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])


@router.get("/teams", response_model=list[TeamSummary])
def list_teams(
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> list[Team]:
    return list(db.scalars(select(Team).order_by(Team.created_at.desc())).all())


@router.post("/teams/{team_id}/decision", response_model=TeamSummary)
def team_decision(
    team_id: str,
    payload: TeamDecision,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Team:
    team = db.scalar(select(Team).options(joinedload(Team.users)).where(Team.id == team_id))
    if not team:
        raise HTTPException(status_code=404, detail="ไม่พบทีม")
    try:
        return decide_team(db, admin, team, payload.action, payload.reason)
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/slots", response_model=SlotSummary, status_code=201)
def admin_create_slot(
    payload: SlotCreate,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Slot:
    try:
        return create_slot(db, admin, **payload.model_dump())
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.patch("/slots/{slot_id}/open", response_model=SlotSummary)
def toggle_slot(
    slot_id: str,
    is_open: bool,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Slot:
    slot = db.get(Slot, slot_id)
    if not slot:
        raise HTTPException(status_code=404, detail="ไม่พบ Slot")
    slot.is_open = is_open
    audit(db, admin.id, "SLOT_OPEN_CHANGED", "slot", slot.id, {"is_open": is_open})
    db.commit()
    db.refresh(slot)
    return slot


@router.patch("/bookings/{booking_id}/queue", response_model=BookingSummary)
def adjust_queue(
    booking_id: str,
    payload: QueueAdjust,
    admin: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
) -> Booking:
    booking = db.get(Booking, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="ไม่พบ Booking")
    if payload.confirm:
        if booking.status == BookingStatus.WAITLISTED.value:
            slot = db.get(Slot, booking.slot_id)
            confirmed_count = db.scalar(
                select(func.count(Booking.id)).where(
                    Booking.slot_id == booking.slot_id,
                    Booking.status == BookingStatus.CONFIRMED.value,
                )
            ) or 0
            if slot and confirmed_count >= slot.capacity:
                raise HTTPException(status_code=409, detail="Slot เต็มตาม Capacity กรุณายกเลิก Booking อื่นหรือเพิ่ม Capacity")
        booking.status = BookingStatus.CONFIRMED.value
        booking.queue_position = 0
    else:
        booking.status = BookingStatus.WAITLISTED.value
        booking.queue_position = payload.queue_position or 1
    audit(
        db,
        admin.id,
        "BOOKING_QUEUE_ADJUSTED",
        "booking",
        booking.id,
        {"status": booking.status, "queue_position": booking.queue_position},
    )
    db.commit()
    db.refresh(booking)
    return booking
