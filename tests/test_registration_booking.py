from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import BookingStatus, Team, TeamStatus, UserRole, VPNCredential
from app.services import (
    BusinessRuleError,
    book_slot,
    cancel_booking,
    create_slot,
    create_team_member,
    decide_team,
    register_team,
    seed_admin,
)


def approved_team_with_two_members(db):
    admin = seed_admin(db)
    team = register_team(
        db,
        team_name="Autonomous Eagles",
        institution="NT Drone University",
        faculty="Engineering",
        leader_name="Leader One",
        email="leader1@example.local",
        phone="0800001001",
        username="leader.one",
        password="Leader-Team-123!",
        document_name="registration.pdf",
        document_content=b"%PDF-1.4\nregistration\n",
        document_mime_type="application/pdf",
    )
    team = db.execute(
        select(Team).options(joinedload(Team.users)).where(Team.id == team.id)
    ).unique().scalar_one()
    leader = next(user for user in team.users if user.role == UserRole.TEAM_LEADER.value)
    assert leader.is_active is False
    decide_team(db, admin, team, "approve")
    assert team.status == TeamStatus.APPROVED.value
    assert leader.is_active is True
    member = create_team_member(
        db,
        leader,
        full_name="Member Two",
        username="member.two",
        email="member2@example.local",
        phone="0800001002",
        password="Member-Team-123!",
    )
    assert member.is_active
    return admin, team, leader


def test_registration_approval_vpn_and_booking(db):
    admin, team, leader = approved_team_with_two_members(db)
    vpn = db.scalar(select(VPNCredential).where(VPNCredential.team_id == team.id))
    assert vpn is not None
    assert vpn.enabled is False

    slot = create_slot(
        db,
        admin,
        name="Round 1",
        starts_at=datetime.now(timezone.utc) + timedelta(hours=2),
        duration_minutes=180,
        capacity=1,
        resource_key="sim-01",
    )
    booking = book_slot(db, leader, slot)
    assert booking.status == BookingStatus.CONFIRMED.value
    assert booking.queue_position == 0

    cancelled = cancel_booking(db, leader, booking)
    assert cancelled.status == BookingStatus.CANCELLED.value
    assert cancelled.cooldown_until is not None

    with pytest.raises(BusinessRuleError, match="Cooldown"):
        book_slot(db, leader, slot)
