from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.dependencies import get_current_user, require_roles
from app.models import Booking, Slot, User, UserRole
from app.schemas import BookingCreate, BookingSummary, SlotSummary
from app.services import BusinessRuleError, book_slot, cancel_booking

router = APIRouter(prefix="/api/v1", tags=["Booking"])


@router.get("/slots", response_model=list[SlotSummary])
def list_slots(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> list[Slot]:
    return list(
        db.scalars(
            select(Slot)
            .where(Slot.is_open.is_(True), Slot.starts_at > datetime.now(timezone.utc))
            .order_by(Slot.starts_at.asc())
        ).all()
    )


@router.get("/bookings", response_model=list[BookingSummary])
def list_bookings(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[Booking]:
    query = select(Booking).order_by(Booking.created_at.desc())
    if user.role != UserRole.ADMIN.value:
        if not user.team_id:
            return []
        query = query.where(Booking.team_id == user.team_id)
    return list(db.scalars(query).all())


@router.post("/bookings", response_model=BookingSummary, status_code=201)
def create_booking(
    payload: BookingCreate,
    leader: User = Depends(require_roles(UserRole.TEAM_LEADER)),
    db: Session = Depends(get_db),
) -> Booking:
    slot = db.get(Slot, payload.slot_id)
    if not slot:
        raise HTTPException(status_code=404, detail="ไม่พบ Slot")
    try:
        return book_slot(db, leader, slot)
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/bookings/{booking_id}/cancel", response_model=BookingSummary)
def cancel(
    booking_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Booking:
    booking = db.scalar(select(Booking).options(joinedload(Booking.slot)).where(Booking.id == booking_id))
    if not booking:
        raise HTTPException(status_code=404, detail="ไม่พบ Booking")
    try:
        return cancel_booking(db, user, booking)
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
