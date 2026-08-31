from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.config import get_settings
from app.db import SessionLocal, create_schema
from app.models import Slot, Team, UserRole
from app.services import create_slot, create_team_member, decide_team, register_team, seed_admin


def main() -> None:
    settings = get_settings()
    create_schema()
    with SessionLocal() as db:
        admin = seed_admin(db, settings)
        team = db.scalar(select(Team).options(joinedload(Team.users)).where(Team.name == "Demo Flight Lab"))
        if not team:
            team = register_team(
                db,
                team_name="Demo Flight Lab",
                institution="NT Drone Academy",
                faculty="Autonomous Systems",
                leader_name="Demo Team Leader",
                email="leader@example.local",
                phone="0800000001",
                username="demo.leader",
                password="Demo-Team-123!",
                document_name="demo-registration.pdf",
                document_content=b"%PDF-1.4\n% NT Drone demo registration\n",
                document_mime_type="application/pdf",
                settings=settings,
            )
            team = db.scalar(select(Team).options(joinedload(Team.users)).where(Team.id == team.id))
            decide_team(db, admin, team, "approve", settings=settings)
            leader = next(user for user in team.users if user.role == UserRole.TEAM_LEADER.value)
            create_team_member(
                db,
                leader,
                full_name="Demo Member",
                username="demo.member",
                email="member@example.local",
                phone="0800000002",
                password="Demo-Team-123!",
            )

        if not db.scalar(select(Slot.id).limit(1)):
            base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
            for index in range(3):
                create_slot(
                    db,
                    admin,
                    name=f"Demo Round {index + 1}",
                    starts_at=base + timedelta(hours=index * 4),
                    duration_minutes=settings.slot_default_duration_minutes,
                    capacity=1,
                    resource_key="sim-01",
                )

    print("Demo data ready")
    print("Leader: demo.leader / Demo-Team-123!")
    print("Member: demo.member / Demo-Team-123!")
    print("Admin credentials come from ADMIN_USERNAME and ADMIN_PASSWORD")


if __name__ == "__main__":
    main()
