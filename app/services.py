from __future__ import annotations

import base64
import json
import os
import secrets
import smtplib
import string
import subprocess
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from ipaddress import ip_network
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.models import (
    AuditLog,
    Booking,
    BookingStatus,
    Feedback,
    Notification,
    NotificationChannel,
    NotificationStatus,
    RegistrationDocument,
    SimulationMode,
    SimulationRun,
    SimulationStatus,
    Slot,
    Team,
    TeamStatus,
    User,
    UserRole,
    VPNCredential,
    utcnow,
)
from app.security import decrypt_secret, encrypt_secret, hash_password, validate_password_strength


class BusinessRuleError(ValueError):
    """Raised when a portal policy rejects an otherwise valid request."""


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def slot_ends_at(slot: Slot) -> datetime:
    return ensure_utc(slot.starts_at) + timedelta(minutes=slot.duration_minutes)


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def from_json(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def audit(
    db: Session,
    actor_user_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        metadata_json=to_json(metadata or {}),
    )
    db.add(row)
    return row


def queue_notification(
    db: Session,
    channel: NotificationChannel | str,
    destination: str,
    subject: str,
    body: str,
) -> Notification:
    row = Notification(
        channel=channel.value if isinstance(channel, NotificationChannel) else channel,
        destination=destination,
        subject=subject,
        body=body,
        status=NotificationStatus.PENDING.value,
    )
    db.add(row)
    return row


def generate_team_code(db: Session) -> str:
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(25):
        candidate = "NTD-" + "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.scalar(select(Team.id).where(Team.code == candidate)):
            return candidate
    raise RuntimeError("ไม่สามารถสร้าง Team ID ที่ไม่ซ้ำได้")


def normalize_username(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or any(ch not in string.ascii_lowercase + string.digits + "._-" for ch in normalized):
        raise BusinessRuleError("Username ใช้ได้เฉพาะ a-z, 0-9, จุด, ขีดกลาง และขีดล่าง")
    return normalized


def validate_unique_user(db: Session, username: str, email: str) -> None:
    username = normalize_username(username)
    email = email.strip().lower()
    existing = db.scalar(
        select(User).where(or_(func.lower(User.username) == username, func.lower(User.email) == email))
    )
    if existing:
        if existing.username.lower() == username:
            raise BusinessRuleError("Username นี้ถูกใช้งานแล้ว")
        raise BusinessRuleError("E-mail นี้ถูกใช้งานแล้ว")


def save_registration_document(
    settings: Settings,
    team_id: str,
    original_name: str,
    content: bytes,
    mime_type: str,
) -> str:
    if not content:
        raise BusinessRuleError("กรุณาแนบเอกสารประกอบการสมัคร")
    if len(content) > settings.max_upload_bytes:
        raise BusinessRuleError(f"ไฟล์ต้องไม่เกิน {settings.max_upload_bytes // (1024 * 1024)} MB")
    suffix = Path(original_name).suffix.lower()
    allowed = {".pdf", ".png", ".jpg", ".jpeg"}
    if suffix not in allowed:
        raise BusinessRuleError("รองรับเอกสาร PDF, PNG, JPG และ JPEG เท่านั้น")
    safe_name = f"registration-{secrets.token_hex(6)}{suffix}"
    folder = settings.storage_root / "teams" / team_id / "documents"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / safe_name
    path.write_bytes(content)
    return str(path)


def register_team(
    db: Session,
    *,
    team_name: str,
    institution: str,
    faculty: str | None,
    leader_name: str,
    email: str,
    phone: str,
    username: str,
    password: str,
    document_name: str,
    document_content: bytes,
    document_mime_type: str,
    settings: Settings | None = None,
) -> Team:
    settings = settings or get_settings()
    team_name = team_name.strip()
    institution = institution.strip()
    leader_name = leader_name.strip()
    email = email.strip().lower()
    username = normalize_username(username)

    if len(team_name) < 2 or len(institution) < 2 or len(leader_name) < 2:
        raise BusinessRuleError("กรอกชื่อทีม สถาบัน และชื่อหัวหน้าทีมให้ครบถ้วน")
    validate_password_strength(password)
    validate_unique_user(db, username, email)
    if db.scalar(select(Team.id).where(func.lower(Team.name) == team_name.lower())):
        raise BusinessRuleError("ชื่อทีมนี้ถูกใช้งานแล้ว")

    team = Team(
        code=generate_team_code(db),
        name=team_name,
        institution=institution,
        faculty=(faculty or "").strip() or None,
        status=TeamStatus.PENDING.value,
    )
    db.add(team)
    db.flush()

    leader = User(
        team_id=team.id,
        username=username,
        email=email,
        full_name=leader_name,
        phone=phone.strip(),
        password_hash=hash_password(password),
        role=UserRole.TEAM_LEADER.value,
        is_active=False,
    )
    db.add(leader)

    storage_path = save_registration_document(
        settings,
        team.id,
        document_name,
        document_content,
        document_mime_type,
    )
    db.add(
        RegistrationDocument(
            team_id=team.id,
            original_name=document_name,
            storage_path=storage_path,
            mime_type=document_mime_type,
        )
    )

    for child in ("config", "output", "blind_sim"):
        (settings.storage_root / "teams" / team.id / child).mkdir(parents=True, exist_ok=True)

    audit(db, None, "TEAM_REGISTERED", "team", team.id, {"team_code": team.code})
    queue_notification(
        db,
        NotificationChannel.SMTP,
        email,
        "รับคำขอสมัครทีม NT Drone แล้ว",
        f"ระบบได้รับคำขอของทีม {team.name} ({team.code}) แล้ว และกำลังรอ Admin ตรวจสอบเอกสาร",
    )
    db.commit()
    db.refresh(team)
    return team


def seed_admin(db: Session, settings: Settings | None = None) -> User:
    settings = settings or get_settings()
    existing = db.scalar(select(User).where(User.role == UserRole.ADMIN.value))
    if existing:
        return existing
    validate_password_strength(settings.admin_password)
    admin = User(
        username=normalize_username(settings.admin_username),
        email=settings.admin_email.lower(),
        full_name="NT Drone Administrator",
        phone="-",
        password_hash=hash_password(settings.admin_password),
        role=UserRole.ADMIN.value,
        is_active=True,
        totp_enabled=False,
    )
    db.add(admin)
    audit(db, None, "ADMIN_SEEDED", "user", admin.id)
    db.commit()
    db.refresh(admin)
    return admin


def create_team_member(
    db: Session,
    leader: User,
    *,
    full_name: str,
    username: str,
    email: str,
    phone: str,
    password: str,
) -> User:
    if leader.role != UserRole.TEAM_LEADER.value or not leader.team_id:
        raise BusinessRuleError("เฉพาะหัวหน้าทีมเท่านั้นที่เพิ่มสมาชิกได้")
    team = db.get(Team, leader.team_id)
    if not team or team.status != TeamStatus.APPROVED.value:
        raise BusinessRuleError("ทีมต้องได้รับอนุมัติก่อนเพิ่มสมาชิก")
    member_count = db.scalar(select(func.count(User.id)).where(User.team_id == team.id)) or 0
    if member_count >= 4:
        raise BusinessRuleError("หนึ่งทีมมีสมาชิกได้สูงสุด 4 คน รวมหัวหน้าทีม")
    validate_password_strength(password)
    validate_unique_user(db, username, email)
    member = User(
        team_id=team.id,
        username=normalize_username(username),
        email=email.strip().lower(),
        full_name=full_name.strip(),
        phone=phone.strip(),
        password_hash=hash_password(password),
        role=UserRole.MEMBER.value,
        is_active=True,
    )
    db.add(member)
    db.flush()
    audit(db, leader.id, "TEAM_MEMBER_CREATED", "user", member.id, {"team_id": team.id})
    queue_notification(
        db,
        NotificationChannel.SMTP,
        member.email,
        "บัญชีสมาชิก NT Drone พร้อมใช้งาน",
        f"บัญชี {member.username} สำหรับทีม {team.name} ถูกสร้างแล้ว กรุณาเข้าสู่ระบบและตั้งค่า 2FA",
    )
    db.commit()
    db.refresh(member)
    return member


def _generate_wireguard_keypair(settings: Settings) -> tuple[str, str]:
    if settings.vpn_driver == "wireguard":
        try:
            private_key = subprocess.run(
                ["wg", "genkey"], check=True, capture_output=True, text=True, timeout=10
            ).stdout.strip()
            public_key = subprocess.run(
                ["wg", "pubkey"],
                input=private_key + "\n",
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
            return private_key, public_key
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise BusinessRuleError("สร้าง WireGuard key ไม่สำเร็จ ตรวจสอบคำสั่ง wg และสิทธิ์ของระบบ") from exc
    private_raw = os.urandom(32)
    public_raw = os.urandom(32)
    return base64.b64encode(private_raw).decode("ascii"), base64.b64encode(public_raw).decode("ascii")


def _next_vpn_address(db: Session, settings: Settings) -> str:
    network = ip_network(settings.vpn_client_network, strict=False)
    used = set(db.scalars(select(VPNCredential.address)).all())
    hosts = list(network.hosts())
    # Reserve the first host for the WireGuard server.
    for host in hosts[1:]:
        candidate = f"{host}/{network.max_prefixlen}"
        if candidate not in used:
            return candidate
    raise BusinessRuleError("VPN client network ไม่มี IP ว่าง")


def ensure_vpn_credential(db: Session, team: Team, settings: Settings | None = None) -> VPNCredential:
    settings = settings or get_settings()
    existing = db.scalar(select(VPNCredential).where(VPNCredential.team_id == team.id))
    if existing:
        return existing
    private_key, public_key = _generate_wireguard_keypair(settings)
    credential = VPNCredential(
        team_id=team.id,
        public_key=public_key,
        private_key_encrypted=encrypt_secret(private_key, settings),
        address=_next_vpn_address(db, settings),
        enabled=False,
    )
    db.add(credential)
    db.flush()
    return credential


def decide_team(
    db: Session,
    admin: User,
    team: Team,
    action: str,
    reason: str | None = None,
    settings: Settings | None = None,
) -> Team:
    settings = settings or get_settings()
    if admin.role != UserRole.ADMIN.value:
        raise BusinessRuleError("เฉพาะ Admin เท่านั้นที่อนุมัติทีมได้")
    if action == "approve":
        team.status = TeamStatus.APPROVED.value
        team.rejection_reason = None
        team.approved_at = utcnow()
        team.approved_by_user_id = admin.id
        for user in team.users:
            user.is_active = True
        ensure_vpn_credential(db, team, settings)
        subject = "ทีม NT Drone ได้รับอนุมัติแล้ว"
        body = (
            f"ทีม {team.name} ({team.code}) ได้รับอนุมัติแล้ว กรุณาเข้าสู่ระบบ ตั้งค่า 2FA "
            "เพิ่มสมาชิกให้ครบ 2–4 คน และดาวน์โหลด VPN config จาก Portal"
        )
        audit(db, admin.id, "TEAM_APPROVED", "team", team.id)
    elif action == "reject":
        team.status = TeamStatus.REJECTED.value
        team.rejection_reason = (reason or "ข้อมูลหรือเอกสารไม่ผ่านการตรวจสอบ").strip()
        for user in team.users:
            user.is_active = False
        subject = "ผลตรวจสอบคำขอสมัครทีม NT Drone"
        body = f"ทีม {team.name} ยังไม่ผ่านการอนุมัติ เหตุผล: {team.rejection_reason}"
        audit(db, admin.id, "TEAM_REJECTED", "team", team.id, {"reason": team.rejection_reason})
    else:
        raise BusinessRuleError("action ต้องเป็น approve หรือ reject")

    leader = next((user for user in team.users if user.role == UserRole.TEAM_LEADER.value), None)
    if leader:
        queue_notification(db, NotificationChannel.SMTP, leader.email, subject, body)
        if settings.sms_webhook_url and leader.phone and leader.phone != "-":
            queue_notification(db, NotificationChannel.SMS, leader.phone, subject, body)
    db.commit()
    db.refresh(team)
    return team


def create_slot(
    db: Session,
    admin: User,
    *,
    name: str,
    starts_at: datetime,
    duration_minutes: int,
    capacity: int,
    resource_key: str,
) -> Slot:
    if admin.role != UserRole.ADMIN.value:
        raise BusinessRuleError("เฉพาะ Admin เท่านั้นที่สร้าง Slot ได้")
    starts_at = ensure_utc(starts_at)
    if starts_at <= utcnow():
        raise BusinessRuleError("เวลาเริ่ม Slot ต้องอยู่ในอนาคต")
    if duration_minutes < 30 or duration_minutes > 1440:
        raise BusinessRuleError("Duration ต้องอยู่ระหว่าง 30–1,440 นาที")
    if capacity < 1:
        raise BusinessRuleError("Capacity ต้องไม่น้อยกว่า 1")
    slot = Slot(
        name=name.strip(),
        starts_at=starts_at,
        duration_minutes=duration_minutes,
        capacity=capacity,
        resource_key=resource_key.strip(),
        created_by_user_id=admin.id,
        is_open=True,
    )
    db.add(slot)
    db.flush()
    audit(db, admin.id, "SLOT_CREATED", "slot", slot.id, {"starts_at": starts_at.isoformat()})
    db.commit()
    db.refresh(slot)
    return slot


def _intervals_overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def book_slot(db: Session, leader: User, slot: Slot, settings: Settings | None = None) -> Booking:
    settings = settings or get_settings()
    if leader.role != UserRole.TEAM_LEADER.value or not leader.team_id:
        raise BusinessRuleError("เฉพาะหัวหน้าทีมเท่านั้นที่จอง Slot ได้")
    team = db.get(Team, leader.team_id)
    if not team or team.status != TeamStatus.APPROVED.value:
        raise BusinessRuleError("ทีมยังไม่ได้รับอนุมัติ")
    member_count = db.scalar(select(func.count(User.id)).where(User.team_id == team.id, User.is_active.is_(True))) or 0
    if not 2 <= member_count <= 4:
        raise BusinessRuleError("ต้องมีสมาชิกทีม 2–4 คน รวมหัวหน้าทีม ก่อนจอง Slot")
    if not slot.is_open:
        raise BusinessRuleError("Slot นี้ปิดรับการจองแล้ว")
    now = utcnow()
    if ensure_utc(slot.starts_at) <= now:
        raise BusinessRuleError("ไม่สามารถจอง Slot ที่เริ่มแล้ว")

    existing_same_slot = db.scalar(
        select(Booking).where(
            Booking.team_id == team.id,
            Booking.slot_id == slot.id,
            Booking.status.in_([BookingStatus.CONFIRMED.value, BookingStatus.WAITLISTED.value]),
        )
    )
    if existing_same_slot:
        raise BusinessRuleError("ทีมจอง Slot นี้อยู่แล้ว")

    latest_cancelled = db.scalar(
        select(Booking)
        .where(Booking.team_id == team.id, Booking.status == BookingStatus.CANCELLED.value)
        .order_by(Booking.cancelled_at.desc())
        .limit(1)
    )
    if latest_cancelled and latest_cancelled.cooldown_until and ensure_utc(latest_cancelled.cooldown_until) > now:
        remaining = int((ensure_utc(latest_cancelled.cooldown_until) - now).total_seconds())
        raise BusinessRuleError(f"อยู่ในช่วง Cooldown กรุณารออีก {remaining} วินาที")

    requested_start = ensure_utc(slot.starts_at)
    requested_end = slot_ends_at(slot)
    team_bookings = db.scalars(
        select(Booking)
        .options(joinedload(Booking.slot))
        .where(
            Booking.team_id == team.id,
            Booking.status.in_([BookingStatus.CONFIRMED.value, BookingStatus.WAITLISTED.value]),
        )
    ).all()
    for current in team_bookings:
        if _intervals_overlap(requested_start, requested_end, ensure_utc(current.slot.starts_at), slot_ends_at(current.slot)):
            raise BusinessRuleError(f"เวลาชนกับ Slot ที่จองไว้: {current.slot.name}")

    confirmed_count = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.slot_id == slot.id,
            Booking.status == BookingStatus.CONFIRMED.value,
        )
    ) or 0
    if confirmed_count < slot.capacity:
        status_value = BookingStatus.CONFIRMED.value
        queue_position = 0
    else:
        max_queue = db.scalar(
            select(func.max(Booking.queue_position)).where(
                Booking.slot_id == slot.id,
                Booking.status == BookingStatus.WAITLISTED.value,
            )
        ) or 0
        status_value = BookingStatus.WAITLISTED.value
        queue_position = max_queue + 1

    booking = Booking(
        team_id=team.id,
        slot_id=slot.id,
        status=status_value,
        queue_position=queue_position,
    )
    db.add(booking)
    db.flush()
    audit(
        db,
        leader.id,
        "SLOT_BOOKED",
        "booking",
        booking.id,
        {"slot_id": slot.id, "status": status_value, "queue_position": queue_position},
    )
    queue_notification(
        db,
        NotificationChannel.SMTP,
        leader.email,
        "ผลการจอง Slot NT Drone",
        f"ทีม {team.name} จอง {slot.name} แล้ว สถานะ {status_value} เวลา {requested_start.isoformat()}",
    )
    db.commit()
    db.refresh(booking)
    return booking


def promote_waitlist(db: Session, slot_id: str) -> Booking | None:
    waiting = db.scalar(
        select(Booking)
        .where(Booking.slot_id == slot_id, Booking.status == BookingStatus.WAITLISTED.value)
        .order_by(Booking.queue_position.asc(), Booking.created_at.asc())
        .limit(1)
    )
    if not waiting:
        return None
    waiting.status = BookingStatus.CONFIRMED.value
    waiting.queue_position = 0
    remaining = db.scalars(
        select(Booking)
        .where(Booking.slot_id == slot_id, Booking.status == BookingStatus.WAITLISTED.value)
        .order_by(Booking.queue_position.asc(), Booking.created_at.asc())
    ).all()
    for index, row in enumerate(remaining, start=1):
        row.queue_position = index
    leader = db.scalar(
        select(User).where(User.team_id == waiting.team_id, User.role == UserRole.TEAM_LEADER.value)
    )
    if leader:
        queue_notification(
            db,
            NotificationChannel.SMTP,
            leader.email,
            "คิว Slot ได้รับการยืนยันแล้ว",
            "คิวสำรองของทีมได้รับการเลื่อนเป็น CONFIRMED กรุณาตรวจสอบรายละเอียดใน Portal",
        )
    return waiting


def cancel_booking(db: Session, actor: User, booking: Booking, settings: Settings | None = None) -> Booking:
    settings = settings or get_settings()
    is_admin = actor.role == UserRole.ADMIN.value
    is_owner_leader = actor.role == UserRole.TEAM_LEADER.value and actor.team_id == booking.team_id
    if not (is_admin or is_owner_leader):
        raise BusinessRuleError("ไม่มีสิทธิ์ยกเลิก Booking นี้")
    if booking.status not in {BookingStatus.CONFIRMED.value, BookingStatus.WAITLISTED.value}:
        raise BusinessRuleError("Booking นี้ยกเลิกไม่ได้")
    old_status = booking.status
    booking.status = BookingStatus.CANCELLED.value
    booking.cancelled_at = utcnow()
    booking.cooldown_until = utcnow() + timedelta(seconds=settings.booking_cooldown_seconds)
    booking.queue_position = 0
    if old_status == BookingStatus.CONFIRMED.value:
        promote_waitlist(db, booking.slot_id)
    else:
        remaining = db.scalars(
            select(Booking)
            .where(Booking.slot_id == booking.slot_id, Booking.status == BookingStatus.WAITLISTED.value)
            .order_by(Booking.queue_position.asc(), Booking.created_at.asc())
        ).all()
        for index, row in enumerate(remaining, start=1):
            row.queue_position = index
    audit(db, actor.id, "BOOKING_CANCELLED", "booking", booking.id)
    db.commit()
    db.refresh(booking)
    return booking


def active_booking_for_team(db: Session, team_id: str, now: datetime | None = None) -> Booking | None:
    now = ensure_utc(now or utcnow())
    bookings = db.scalars(
        select(Booking)
        .options(joinedload(Booking.slot))
        .where(Booking.team_id == team_id, Booking.status == BookingStatus.CONFIRMED.value)
        .order_by(Booking.created_at.desc())
    ).all()
    for booking in bookings:
        if ensure_utc(booking.slot.starts_at) <= now < slot_ends_at(booking.slot):
            return booking
    return None


def render_wireguard_config(credential: VPNCredential, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    private_key = decrypt_secret(credential.private_key_encrypted, settings)
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {credential.address}\n"
        f"DNS = {settings.wireguard_dns}\n\n"
        "[Peer]\n"
        f"PublicKey = {settings.wireguard_server_public_key}\n"
        f"Endpoint = {settings.wireguard_server_endpoint}\n"
        "AllowedIPs = 192.168.248.0/24\n"
        "PersistentKeepalive = 25\n"
    )


def _set_vpn_peer_state(
    credential: VPNCredential,
    enabled: bool,
    settings: Settings,
) -> None:
    if settings.vpn_driver == "mock":
        return
    script = settings.vpn_enable_script if enabled else settings.vpn_disable_script
    command = [str(script), settings.wireguard_interface, credential.public_key]
    if enabled:
        command.append(credential.address)
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, PermissionError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise RuntimeError(f"WireGuard command failed: {detail}") from exc


def sync_vpn_access(db: Session, settings: Settings | None = None, now: datetime | None = None) -> dict[str, int]:
    settings = settings or get_settings()
    now = ensure_utc(now or utcnow())
    enabled_count = 0
    disabled_count = 0
    credentials = db.scalars(select(VPNCredential).options(joinedload(VPNCredential.team))).all()
    for credential in credentials:
        should_enable = (
            credential.team.status == TeamStatus.APPROVED.value
            and active_booking_for_team(db, credential.team_id, now) is not None
        )
        if should_enable and not credential.enabled:
            _set_vpn_peer_state(credential, True, settings)
            credential.enabled = True
            credential.last_enabled_at = now
            enabled_count += 1
            audit(db, None, "VPN_ENABLED_BY_SLOT", "vpn_credential", credential.id)
        elif not should_enable and credential.enabled:
            _set_vpn_peer_state(credential, False, settings)
            credential.enabled = False
            credential.last_disabled_at = now
            disabled_count += 1
            audit(db, None, "VPN_DISABLED_BY_SLOT", "vpn_credential", credential.id)
    db.commit()
    return {"enabled": enabled_count, "disabled": disabled_count}


def create_simulation_run(
    db: Session,
    actor: User,
    booking: Booking,
    *,
    mode: str,
    scenario_name: str,
    config: dict[str, Any],
    settings: Settings | None = None,
) -> SimulationRun:
    settings = settings or get_settings()
    if actor.team_id != booking.team_id and actor.role != UserRole.ADMIN.value:
        raise BusinessRuleError("Booking ไม่ได้อยู่ในทีมของผู้ใช้")
    if booking.status != BookingStatus.CONFIRMED.value:
        raise BusinessRuleError("Simulation ใช้ได้เฉพาะ Booking สถานะ CONFIRMED")
    mode = mode.upper()
    if mode not in {SimulationMode.STANDARD.value, SimulationMode.BLIND.value}:
        raise BusinessRuleError("Mode ต้องเป็น STANDARD หรือ BLIND")

    existing = db.scalar(
        select(SimulationRun).where(
            SimulationRun.booking_id == booking.id,
            SimulationRun.status.in_([
                SimulationStatus.READY.value,
                SimulationStatus.QUEUED.value,
                SimulationStatus.RUNNING.value,
            ]),
        )
    )
    if existing:
        raise BusinessRuleError("Booking นี้มี Simulation ที่ยังไม่จบอยู่แล้ว")

    status_value = SimulationStatus.READY.value if mode == SimulationMode.STANDARD.value else SimulationStatus.QUEUED.value
    run = SimulationRun(
        team_id=booking.team_id,
        booking_id=booking.id,
        mode=mode,
        scenario_name=scenario_name.strip() or "default",
        status=status_value,
        config_json=to_json(config),
    )
    db.add(run)
    db.flush()
    config_dir = settings.storage_root / "teams" / booking.team_id / (
        "blind_sim" if mode == SimulationMode.BLIND.value else "config"
    )
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{run.id}.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    audit(db, actor.id, "SIMULATION_CREATED", "simulation_run", run.id, {"mode": mode})
    db.commit()
    db.refresh(run)
    return run


def _call_simulator_start(run: SimulationRun, settings: Settings) -> str:
    if settings.simulation_driver == "mock":
        return f"mock-{run.id}"
    if not settings.simulator_api_url:
        raise RuntimeError("SIMULATOR_API_URL is required for SIMULATION_DRIVER=http")
    payload = {
        "run_id": run.id,
        "team_id": run.team_id,
        "booking_id": run.booking_id,
        "mode": run.mode,
        "scenario_name": run.scenario_name,
        "config": from_json(run.config_json),
        "callback_url": f"{settings.base_url}/api/v1/simulator/runs/{run.id}/result",
    }
    response = httpx.post(
        f"{settings.simulator_api_url.rstrip('/')}/runs",
        json=payload,
        headers={"Authorization": f"Bearer {settings.simulator_api_token}"},
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return str(data.get("run_id") or data.get("id") or run.id)


def start_simulation_run(
    db: Session,
    actor: User | None,
    run: SimulationRun,
    settings: Settings | None = None,
) -> SimulationRun:
    settings = settings or get_settings()
    if actor and actor.role != UserRole.ADMIN.value and actor.team_id != run.team_id:
        raise BusinessRuleError("ไม่มีสิทธิ์ควบคุม Simulation นี้")
    if run.status not in {SimulationStatus.READY.value, SimulationStatus.QUEUED.value}:
        raise BusinessRuleError("Simulation ไม่อยู่ในสถานะที่เริ่มได้")
    booking = db.scalar(select(Booking).options(joinedload(Booking.slot)).where(Booking.id == run.booking_id))
    if not booking or not (
        ensure_utc(booking.slot.starts_at) <= utcnow() < slot_ends_at(booking.slot)
    ):
        raise BusinessRuleError("เริ่ม Simulation ได้เฉพาะช่วงเวลาของ Slot")
    vpn = db.scalar(select(VPNCredential).where(VPNCredential.team_id == run.team_id))
    if not vpn or not vpn.enabled:
        raise BusinessRuleError("VPN ของทีมยังไม่เปิดตาม Slot")

    run.remote_run_id = _call_simulator_start(run, settings)
    run.status = SimulationStatus.RUNNING.value
    run.started_at = utcnow()
    run.metrics_json = to_json({"status": "starting", "completion_percent": 0})
    audit(db, actor.id if actor else None, "SIMULATION_STARTED", "simulation_run", run.id)
    db.commit()
    db.refresh(run)
    return run


def control_simulation_run(
    db: Session,
    actor: User,
    run: SimulationRun,
    action: str,
    settings: Settings | None = None,
) -> SimulationRun:
    settings = settings or get_settings()
    if actor.role != UserRole.ADMIN.value and actor.team_id != run.team_id:
        raise BusinessRuleError("ไม่มีสิทธิ์ควบคุม Simulation นี้")
    action = action.lower()
    if action not in {"stop", "cancel"}:
        raise BusinessRuleError("action ต้องเป็น stop หรือ cancel")
    if run.status not in {
        SimulationStatus.READY.value,
        SimulationStatus.QUEUED.value,
        SimulationStatus.RUNNING.value,
    }:
        raise BusinessRuleError("Simulation นี้ไม่สามารถเปลี่ยนสถานะได้")

    if settings.simulation_driver == "http" and run.remote_run_id and settings.simulator_api_url:
        response = httpx.post(
            f"{settings.simulator_api_url.rstrip('/')}/runs/{run.remote_run_id}/{action}",
            headers={"Authorization": f"Bearer {settings.simulator_api_token}"},
            timeout=20,
        )
        response.raise_for_status()

    run.status = SimulationStatus.STOPPED.value if action == "stop" else SimulationStatus.CANCELLED.value
    run.ended_at = utcnow()
    audit(db, actor.id, f"SIMULATION_{action.upper()}", "simulation_run", run.id)
    db.commit()
    db.refresh(run)
    return run


def claim_next_blind_run(
    db: Session,
    settings: Settings | None = None,
    *,
    dispatch: bool = True,
) -> SimulationRun | None:
    settings = settings or get_settings()
    candidates = db.scalars(
        select(SimulationRun)
        .options(joinedload(SimulationRun.booking).joinedload(Booking.slot))
        .where(
            SimulationRun.mode == SimulationMode.BLIND.value,
            SimulationRun.status == SimulationStatus.QUEUED.value,
        )
        .order_by(SimulationRun.created_at.asc(), SimulationRun.team_id.asc())
    ).all()
    for run in candidates:
        now = utcnow()
        if not (ensure_utc(run.booking.slot.starts_at) <= now < slot_ends_at(run.booking.slot)):
            continue
        vpn = db.scalar(select(VPNCredential).where(VPNCredential.team_id == run.team_id))
        if not vpn or not vpn.enabled:
            continue
        if dispatch:
            return start_simulation_run(db, None, run, settings)
        run.status = SimulationStatus.RUNNING.value
        run.started_at = utcnow()
        run.remote_run_id = run.remote_run_id or f"pull-{run.id}"
        run.metrics_json = to_json({"status": "claimed", "completion_percent": 0})
        audit(db, None, "SIMULATION_CLAIMED_BY_SIMULATOR", "simulation_run", run.id)
        db.commit()
        db.refresh(run)
        return run
    return None


def update_simulation_result(
    db: Session,
    run: SimulationRun,
    *,
    status_value: str,
    metrics: dict[str, Any],
    result: dict[str, Any],
    output_path: str | None,
    remote_run_id: str | None,
    settings: Settings | None = None,
) -> SimulationRun:
    settings = settings or get_settings()
    allowed = {
        SimulationStatus.RUNNING.value,
        SimulationStatus.COMPLETED.value,
        SimulationStatus.FAILED.value,
        SimulationStatus.STOPPED.value,
        SimulationStatus.CANCELLED.value,
    }
    if status_value not in allowed:
        raise BusinessRuleError("Simulator status ไม่ถูกต้อง")
    run.status = status_value
    run.metrics_json = to_json(metrics)
    run.result_json = to_json(result)
    run.remote_run_id = remote_run_id or run.remote_run_id
    if output_path:
        run.output_path = output_path
    if status_value == SimulationStatus.RUNNING.value and not run.started_at:
        run.started_at = utcnow()
    if status_value in {
        SimulationStatus.COMPLETED.value,
        SimulationStatus.FAILED.value,
        SimulationStatus.STOPPED.value,
        SimulationStatus.CANCELLED.value,
    }:
        run.ended_at = utcnow()
    audit(db, None, "SIMULATOR_RESULT_UPDATED", "simulation_run", run.id, {"status": status_value})
    db.commit()
    db.refresh(run)
    return run


def advance_mock_simulations(db: Session, settings: Settings | None = None, now: datetime | None = None) -> int:
    settings = settings or get_settings()
    if settings.simulation_driver != "mock":
        return 0
    now = ensure_utc(now or utcnow())
    runs = db.scalars(
        select(SimulationRun).where(SimulationRun.status == SimulationStatus.RUNNING.value)
    ).all()
    changed = 0
    for run in runs:
        if not run.started_at:
            continue
        elapsed = max(0, int((now - ensure_utc(run.started_at)).total_seconds()))
        duration = max(settings.mock_simulation_duration_seconds, 1)
        completion = min(100, int(elapsed / duration * 100))
        run.metrics_json = to_json(
            {
                "status": "running" if completion < 100 else "completed",
                "completion_percent": completion,
                "altitude_m": round(42 + completion * 0.18, 2),
                "speed_mps": round(6.5 + completion * 0.035, 2),
                "distance_m": round(completion * 12.4, 2),
                "telemetry_points": max(1, elapsed * 5),
            }
        )
        if completion >= 100:
            output_dir = settings.storage_root / "teams" / run.team_id / "output" / run.id
            output_dir.mkdir(parents=True, exist_ok=True)
            result = {
                "score": 87,
                "summary": "Mock simulation completed successfully",
                "violations": [],
                "artifacts": ["telemetry.json", "flight-summary.json"],
            }
            (output_dir / "flight-summary.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (output_dir / "telemetry.json").write_text(run.metrics_json, encoding="utf-8")
            run.result_json = to_json(result)
            run.output_path = str(output_dir)
            run.status = SimulationStatus.COMPLETED.value
            run.ended_at = now
            leader = db.scalar(
                select(User).where(User.team_id == run.team_id, User.role == UserRole.TEAM_LEADER.value)
            )
            if leader:
                queue_notification(
                    db,
                    NotificationChannel.SMTP,
                    leader.email,
                    "Simulation เสร็จสิ้น",
                    f"Simulation {run.scenario_name} เสร็จสิ้นแล้ว สามารถดูผลและ Telemetry ใน Portal",
                )
            audit(db, None, "SIMULATION_COMPLETED_MOCK", "simulation_run", run.id)
        changed += 1
    if changed:
        db.commit()
    return changed


def mark_completed_bookings(db: Session, now: datetime | None = None) -> int:
    now = ensure_utc(now or utcnow())
    bookings = db.scalars(
        select(Booking).options(joinedload(Booking.slot)).where(Booking.status == BookingStatus.CONFIRMED.value)
    ).all()
    changed = 0
    for booking in bookings:
        if slot_ends_at(booking.slot) <= now:
            booking.status = BookingStatus.COMPLETED.value
            changed += 1
    if changed:
        db.commit()
    return changed


def create_feedback(db: Session, user: User, rating: int, message: str) -> Feedback:
    if not user.team_id:
        raise BusinessRuleError("Admin ไม่สามารถส่ง Feedback ในนามทีม")
    if not 1 <= rating <= 5:
        raise BusinessRuleError("คะแนนต้องอยู่ระหว่าง 1–5")
    feedback = Feedback(team_id=user.team_id, user_id=user.id, rating=rating, message=message.strip())
    db.add(feedback)
    audit(db, user.id, "FEEDBACK_SUBMITTED", "feedback", feedback.id)
    db.commit()
    db.refresh(feedback)
    return feedback


def _send_email(notification: Notification, settings: Settings) -> None:
    if not settings.smtp_host:
        print(f"[notification:email:dev-sink] to={notification.destination} subject={notification.subject}")
        return
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = notification.destination
    message["Subject"] = notification.subject
    message.set_content(notification.body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout_seconds) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(message)


def _send_sms(notification: Notification, settings: Settings) -> None:
    if not settings.sms_webhook_url:
        raise RuntimeError("SMS webhook is not configured")
    headers = {"Content-Type": "application/json"}
    if settings.sms_webhook_token:
        headers["Authorization"] = f"Bearer {settings.sms_webhook_token}"
    response = httpx.post(
        settings.sms_webhook_url,
        json={"to": notification.destination, "message": notification.body},
        headers=headers,
        timeout=10,
    )
    response.raise_for_status()


def process_notifications(db: Session, settings: Settings | None = None, limit: int = 20) -> dict[str, int]:
    settings = settings or get_settings()
    rows = db.scalars(
        select(Notification)
        .where(Notification.status == NotificationStatus.PENDING.value)
        .order_by(Notification.created_at.asc())
        .limit(limit)
    ).all()
    sent = 0
    failed = 0
    for row in rows:
        row.attempts += 1
        try:
            if row.channel == NotificationChannel.SMTP.value:
                _send_email(row, settings)
            elif row.channel == NotificationChannel.SMS.value:
                _send_sms(row, settings)
            else:
                raise RuntimeError(f"Unsupported channel: {row.channel}")
            row.status = NotificationStatus.SENT.value
            row.sent_at = utcnow()
            row.last_error = None
            sent += 1
        except Exception as exc:  # Worker must continue processing other notifications.
            row.last_error = str(exc)[:2000]
            if row.attempts >= 3:
                row.status = NotificationStatus.FAILED.value
            failed += 1
    if rows:
        db.commit()
    return {"sent": sent, "failed": failed}
