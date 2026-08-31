from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import SimulationStatus, Team, UserRole, VPNCredential
from app.services import (
    advance_mock_simulations,
    book_slot,
    create_simulation_run,
    create_slot,
    create_team_member,
    decide_team,
    mark_completed_bookings,
    register_team,
    seed_admin,
    start_simulation_run,
    sync_vpn_access,
)


def test_time_based_vpn_and_mock_simulation(db):
    admin = seed_admin(db)
    team = register_team(
        db,
        team_name="Flight Dynamics",
        institution="NT Academy",
        faculty="Robotics",
        leader_name="Flight Leader",
        email="flight.leader@example.local",
        phone="0800002001",
        username="flight.leader",
        password="Flight-Team-123!",
        document_name="registration.pdf",
        document_content=b"%PDF-1.4\nregistration\n",
        document_mime_type="application/pdf",
    )
    team = db.execute(
        select(Team).options(joinedload(Team.users)).where(Team.id == team.id)
    ).unique().scalar_one()
    leader = next(user for user in team.users if user.role == UserRole.TEAM_LEADER.value)
    decide_team(db, admin, team, "approve")
    create_team_member(
        db,
        leader,
        full_name="Flight Member",
        username="flight.member",
        email="flight.member@example.local",
        phone="0800002002",
        password="Flight-Member-123!",
    )

    slot = create_slot(
        db,
        admin,
        name="Active Round",
        starts_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        duration_minutes=180,
        capacity=1,
        resource_key="sim-01",
    )
    booking = book_slot(db, leader, slot)
    slot.starts_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()

    changed = sync_vpn_access(db)
    assert changed["enabled"] == 1
    vpn = db.scalar(select(VPNCredential).where(VPNCredential.team_id == team.id))
    assert vpn and vpn.enabled

    run = create_simulation_run(
        db,
        leader,
        booking,
        mode="STANDARD",
        scenario_name="waypoint-navigation",
        config={"max_altitude_m": 120},
    )
    start_simulation_run(db, leader, run)
    assert run.status == SimulationStatus.RUNNING.value

    changed_runs = advance_mock_simulations(
        db,
        now=datetime.now(timezone.utc) + timedelta(seconds=3),
    )
    assert changed_runs == 1
    db.refresh(run)
    assert run.status == SimulationStatus.COMPLETED.value
    assert run.output_path

    slot.starts_at = datetime.now(timezone.utc) - timedelta(hours=4)
    db.commit()
    disabled = sync_vpn_access(db)
    assert disabled["disabled"] == 1
    assert mark_completed_bookings(db) == 1
    db.refresh(booking)
    assert booking.status == "COMPLETED"
