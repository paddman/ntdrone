from __future__ import annotations

import hmac

import httpx
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import get_optional_user
from app.models import (
    AuditLog,
    Booking,
    BookingStatus,
    Feedback,
    SimulationRun,
    Slot,
    Team,
    TeamStatus,
    User,
    UserRole,
    VPNCredential,
)
from app.security import (
    decrypt_secret,
    encrypt_secret,
    generate_totp_secret,
    get_token_service,
    provisioning_uri,
    qr_data_uri,
    verify_password,
    verify_totp,
)
from app.services import (
    BusinessRuleError,
    audit,
    book_slot,
    cancel_booking,
    control_simulation_run,
    create_feedback,
    create_simulation_run,
    create_slot,
    create_team_member,
    decide_team,
    from_json,
    register_team,
    start_simulation_run,
)

router = APIRouter(tags=["Portal UI"])
TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_ROOT))


def format_datetime(value: datetime | None, timezone_name: str = "Asia/Bangkok") -> str:
    if not value:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(ZoneInfo(timezone_name)).strftime("%d/%m/%Y %H:%M")


def json_pretty(value: str | None) -> str:
    return json.dumps(from_json(value), ensure_ascii=False, indent=2)


templates.env.filters["datetime_th"] = format_datetime
templates.env.filters["json_pretty"] = json_pretty


def redirect(path: str, *, message: str | None = None, error: str | None = None, status_code: int = 303) -> RedirectResponse:
    params: dict[str, str] = {}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    if params:
        separator = "&" if "?" in path else "?"
        path = f"{path}{separator}{urlencode(params)}"
    return RedirectResponse(path, status_code=status_code)


def validate_csrf(request: Request, submitted: str, settings: Settings) -> None:
    cookie = request.cookies.get(settings.csrf_cookie_name, "")
    if not cookie or not submitted or not hmac.compare_digest(cookie, submitted):
        raise HTTPException(status_code=403, detail="CSRF token ไม่ถูกต้อง กรุณาโหลดหน้าใหม่")


def render(
    request: Request,
    template_name: str,
    context: dict[str, object],
    settings: Settings,
    status_code: int = 200,
) -> HTMLResponse:
    csrf_token = request.cookies.get(settings.csrf_cookie_name) or secrets.token_urlsafe(32)
    base_context = {
        "request": request,
        "app_name": settings.app_name,
        "timezone": settings.timezone,
        "csrf_token": csrf_token,
        "message": request.query_params.get("message"),
        "error": request.query_params.get("error"),
    }
    base_context.update(context)
    response = templates.TemplateResponse(name=template_name, request=request, context=base_context, status_code=status_code)
    if request.cookies.get(settings.csrf_cookie_name) != csrf_token:
        response.set_cookie(
            settings.csrf_cookie_name,
            csrf_token,
            max_age=24 * 60 * 60,
            secure=settings.cookie_secure,
            httponly=False,
            samesite="strict",
            path="/",
        )
    return response


def current_user_or_redirect(user: User | None, admin: bool = False) -> User | RedirectResponse:
    if not user:
        return redirect("/login", error="กรุณาเข้าสู่ระบบ")
    if admin and user.role != UserRole.ADMIN.value:
        return redirect("/dashboard", error="ไม่มีสิทธิ์เข้าหน้า Admin")
    return user


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    user: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
) -> HTMLResponse:
    return render(request, "index.html", {"user": user}, settings)


@router.get("/register", response_class=HTMLResponse)
def register_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
) -> Response:
    if user:
        return redirect("/admin" if user.role == UserRole.ADMIN.value else "/dashboard")
    return render(request, "register.html", {"user": None}, settings)


@router.post("/portal/register")
def register_submit(
    request: Request,
    team_name: str = Form(...),
    institution: str = Form(...),
    faculty: str = Form(""),
    leader_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    document: UploadFile = File(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    try:
        content = document.file.read(settings.max_upload_bytes + 1)
        team = register_team(
            db,
            team_name=team_name,
            institution=institution,
            faculty=faculty,
            leader_name=leader_name,
            email=email,
            phone=phone,
            username=username,
            password=password,
            document_name=document.filename or "registration.pdf",
            document_content=content,
            document_mime_type=document.content_type or "application/octet-stream",
            settings=settings,
        )
    except (BusinessRuleError, ValueError) as exc:
        db.rollback()
        return redirect("/register", error=str(exc))
    return redirect("/login", message=f"สมัครทีม {team.code} สำเร็จ รอ Admin ตรวจสอบเอกสาร")


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
) -> Response:
    if user:
        return redirect("/admin" if user.role == UserRole.ADMIN.value else "/dashboard")
    return render(request, "login.html", {"user": None}, settings)


@router.post("/portal/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    user = db.scalar(select(User).where(func.lower(User.username) == username.strip().lower()))
    if not user or not verify_password(user.password_hash, password):
        return redirect("/login", error="Username หรือ Password ไม่ถูกต้อง")
    if not user.is_active:
        return redirect("/login", error="บัญชียังไม่ได้รับอนุมัติหรือถูกปิดใช้งาน")
    if user.team_id:
        team = db.get(Team, user.team_id)
        if not team or team.status != TeamStatus.APPROVED.value:
            return redirect("/login", error="ทีมยังไม่ได้รับอนุมัติ")
    if not user.totp_secret_encrypted:
        user.totp_secret_encrypted = encrypt_secret(generate_totp_secret(), settings)
        db.commit()
    challenge = get_token_service(settings).create_challenge(user.id)
    response = redirect("/two-factor")
    response.set_cookie(
        settings.challenge_cookie_name,
        challenge,
        max_age=settings.challenge_ttl_seconds,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return response


@router.get("/two-factor", response_class=HTMLResponse)
def two_factor_page(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    challenge = request.cookies.get(settings.challenge_cookie_name)
    data = get_token_service(settings).read_challenge(challenge or "")
    if not data:
        return redirect("/login", error="2FA challenge หมดอายุ กรุณา Login ใหม่")
    user = db.get(User, data.get("sub"))
    if not user or not user.totp_secret_encrypted:
        return redirect("/login", error="ไม่พบข้อมูล 2FA")
    secret = decrypt_secret(user.totp_secret_encrypted, settings)
    setup_required = not user.totp_enabled
    uri = provisioning_uri(secret, user.email)
    return render(
        request,
        "two_factor.html",
        {
            "user": None,
            "account": user,
            "setup_required": setup_required,
            "totp_secret": secret if setup_required else None,
            "qr_data_uri": qr_data_uri(uri) if setup_required else None,
        },
        settings,
    )


@router.post("/portal/two-factor")
def two_factor_submit(
    request: Request,
    code: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    challenge = request.cookies.get(settings.challenge_cookie_name)
    data = get_token_service(settings).read_challenge(challenge or "")
    if not data:
        return redirect("/login", error="2FA challenge หมดอายุ กรุณา Login ใหม่")
    user = db.get(User, data.get("sub"))
    if not user or not user.is_active or not user.totp_secret_encrypted:
        return redirect("/login", error="บัญชีหรือ 2FA challenge ไม่ถูกต้อง")
    secret = decrypt_secret(user.totp_secret_encrypted, settings)
    if not verify_totp(secret, code):
        return redirect("/two-factor", error="รหัส 2FA ไม่ถูกต้อง")
    user.totp_enabled = True
    user.last_login_at = datetime.now(timezone.utc)
    audit(db, user.id, "LOGIN_2FA_SUCCESS", "user", user.id)
    db.commit()
    response = redirect("/admin" if user.role == UserRole.ADMIN.value else "/dashboard")
    response.set_cookie(
        settings.session_cookie_name,
        get_token_service(settings).create_session(user.id, user.role),
        max_age=settings.session_ttl_seconds,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
        path="/",
    )
    response.delete_cookie(settings.challenge_cookie_name, path="/")
    return response


@router.post("/portal/logout")
def logout_submit(
    request: Request,
    csrf_token: str = Form(...),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    response = redirect("/", message="ออกจากระบบแล้ว")
    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.challenge_cookie_name, path="/")
    return response


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    checked = current_user_or_redirect(user)
    if isinstance(checked, RedirectResponse):
        return checked
    if checked.role == UserRole.ADMIN.value:
        return redirect("/admin")
    team = db.scalar(select(Team).options(joinedload(Team.users)).where(Team.id == checked.team_id))
    slots = db.scalars(
        select(Slot).where(Slot.is_open.is_(True), Slot.starts_at > datetime.now(timezone.utc)).order_by(Slot.starts_at)
    ).all()
    bookings = db.scalars(
        select(Booking)
        .options(joinedload(Booking.slot))
        .where(Booking.team_id == checked.team_id)
        .order_by(Booking.created_at.desc())
    ).all()
    vpn = db.scalar(select(VPNCredential).where(VPNCredential.team_id == checked.team_id))
    runs = db.scalars(
        select(SimulationRun)
        .options(joinedload(SimulationRun.booking).joinedload(Booking.slot))
        .where(SimulationRun.team_id == checked.team_id)
        .order_by(SimulationRun.created_at.desc())
    ).all()
    return render(
        request,
        "dashboard.html",
        {
            "user": checked,
            "team": team,
            "members": team.users if team else [],
            "slots": slots,
            "bookings": bookings,
            "vpn": vpn,
            "runs": runs,
            "now": datetime.now(timezone.utc),
            "booking_status": BookingStatus,
        },
        settings,
    )


@router.post("/portal/members")
def add_member_submit(
    request: Request,
    full_name: str = Form(...),
    username: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user)
    if isinstance(checked, RedirectResponse):
        return checked
    try:
        member = create_team_member(
            db,
            checked,
            full_name=full_name,
            username=username,
            email=email,
            phone=phone,
            password=password,
        )
    except (BusinessRuleError, ValueError) as exc:
        db.rollback()
        return redirect("/dashboard", error=str(exc))
    return redirect("/dashboard", message=f"เพิ่มสมาชิก {member.username} แล้ว")


@router.post("/portal/bookings")
def booking_submit(
    request: Request,
    slot_id: str = Form(...),
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user)
    if isinstance(checked, RedirectResponse):
        return checked
    slot = db.get(Slot, slot_id)
    if not slot:
        return redirect("/dashboard", error="ไม่พบ Slot")
    try:
        booking = book_slot(db, checked, slot, settings)
    except BusinessRuleError as exc:
        db.rollback()
        return redirect("/dashboard", error=str(exc))
    return redirect("/dashboard", message=f"จอง Slot แล้ว สถานะ {booking.status}")


@router.post("/portal/bookings/{booking_id}/cancel")
def booking_cancel_submit(
    booking_id: str,
    request: Request,
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user)
    if isinstance(checked, RedirectResponse):
        return checked
    booking = db.get(Booking, booking_id)
    if not booking:
        return redirect("/dashboard", error="ไม่พบ Booking")
    try:
        cancel_booking(db, checked, booking, settings)
    except BusinessRuleError as exc:
        db.rollback()
        return redirect("/dashboard", error=str(exc))
    return redirect("/dashboard", message="ยกเลิก Booking แล้ว และเริ่ม Cooldown 5 นาที")


@router.post("/portal/simulations")
def simulation_create_submit(
    request: Request,
    booking_id: str = Form(...),
    mode: str = Form(...),
    scenario_name: str = Form(...),
    config_json: str = Form("{}"),
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user)
    if isinstance(checked, RedirectResponse):
        return checked
    booking = db.get(Booking, booking_id)
    if not booking:
        return redirect("/dashboard", error="ไม่พบ Booking")
    try:
        config = json.loads(config_json or "{}")
        if not isinstance(config, dict):
            raise ValueError("Config JSON ต้องเป็น Object")
        run = create_simulation_run(
            db,
            checked,
            booking,
            mode=mode,
            scenario_name=scenario_name,
            config=config,
            settings=settings,
        )
    except (BusinessRuleError, ValueError, json.JSONDecodeError) as exc:
        db.rollback()
        return redirect("/dashboard", error=f"สร้าง Simulation ไม่สำเร็จ: {exc}")
    return redirect("/dashboard", message=f"สร้าง Simulation {run.scenario_name} สถานะ {run.status}")


@router.post("/portal/simulations/{run_id}/start")
def simulation_start_submit(
    run_id: str,
    request: Request,
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user)
    if isinstance(checked, RedirectResponse):
        return checked
    run = db.get(SimulationRun, run_id)
    if not run:
        return redirect("/dashboard", error="ไม่พบ Simulation")
    try:
        start_simulation_run(db, checked, run, settings)
    except (BusinessRuleError, RuntimeError, httpx.HTTPError) as exc:
        db.rollback()
        return redirect("/dashboard", error=str(exc))
    return redirect("/dashboard", message="เริ่ม Simulation แล้ว")


@router.post("/portal/simulations/{run_id}/{action}")
def simulation_control_submit(
    run_id: str,
    action: str,
    request: Request,
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user)
    if isinstance(checked, RedirectResponse):
        return checked
    run = db.get(SimulationRun, run_id)
    if not run:
        return redirect("/dashboard", error="ไม่พบ Simulation")
    try:
        control_simulation_run(db, checked, run, action, settings)
    except (BusinessRuleError, RuntimeError) as exc:
        db.rollback()
        return redirect("/dashboard", error=str(exc))
    return redirect("/dashboard", message=f"Simulation เปลี่ยนสถานะด้วยคำสั่ง {action}")


@router.post("/portal/feedback")
def feedback_submit(
    request: Request,
    rating: int = Form(...),
    message: str = Form(...),
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user)
    if isinstance(checked, RedirectResponse):
        return checked
    try:
        create_feedback(db, checked, rating, message)
    except BusinessRuleError as exc:
        return redirect("/dashboard", error=str(exc))
    return redirect("/dashboard", message="ส่ง Feedback แล้ว")


@router.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    checked = current_user_or_redirect(user, admin=True)
    if isinstance(checked, RedirectResponse):
        return checked
    teams = db.scalars(
        select(Team).options(joinedload(Team.users), joinedload(Team.documents)).order_by(Team.created_at.desc())
    ).unique().all()
    slots = db.scalars(select(Slot).order_by(Slot.starts_at.desc())).all()
    bookings = db.scalars(
        select(Booking)
        .options(joinedload(Booking.team), joinedload(Booking.slot))
        .order_by(Booking.created_at.desc())
    ).all()
    runs = db.scalars(
        select(SimulationRun).options(joinedload(SimulationRun.team)).order_by(SimulationRun.created_at.desc()).limit(50)
    ).all()
    feedback_rows = db.scalars(select(Feedback).order_by(Feedback.created_at.desc()).limit(20)).all()
    audits = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(30)).all()
    stats = {
        "teams": db.scalar(select(func.count(Team.id))) or 0,
        "pending": db.scalar(select(func.count(Team.id)).where(Team.status == TeamStatus.PENDING.value)) or 0,
        "bookings": db.scalar(select(func.count(Booking.id))) or 0,
        "runs": db.scalar(select(func.count(SimulationRun.id))) or 0,
    }
    return render(
        request,
        "admin.html",
        {
            "user": checked,
            "teams": teams,
            "slots": slots,
            "bookings": bookings,
            "runs": runs,
            "feedback_rows": feedback_rows,
            "audits": audits,
            "stats": stats,
            "team_status": TeamStatus,
        },
        settings,
    )


@router.post("/portal/admin/teams/{team_id}/{action}")
def admin_team_decision_submit(
    team_id: str,
    action: str,
    request: Request,
    reason: str = Form(""),
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user, admin=True)
    if isinstance(checked, RedirectResponse):
        return checked
    team = db.scalar(select(Team).options(joinedload(Team.users)).where(Team.id == team_id))
    if not team:
        return redirect("/admin", error="ไม่พบทีม")
    try:
        decide_team(db, checked, team, action, reason, settings)
    except BusinessRuleError as exc:
        db.rollback()
        return redirect("/admin", error=str(exc))
    return redirect("/admin", message=f"อัปเดตทีม {team.code} เป็น {team.status}")


@router.post("/portal/admin/slots")
def admin_slot_create_submit(
    request: Request,
    name: str = Form(...),
    starts_at_local: str = Form(...),
    duration_minutes: int = Form(180),
    capacity: int = Form(1),
    resource_key: str = Form("sim-01"),
    csrf_token: str = Form(...),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    validate_csrf(request, csrf_token, settings)
    checked = current_user_or_redirect(user, admin=True)
    if isinstance(checked, RedirectResponse):
        return checked
    try:
        local_dt = datetime.fromisoformat(starts_at_local)
        starts_at = local_dt.replace(tzinfo=ZoneInfo(settings.timezone)).astimezone(timezone.utc)
        slot = create_slot(
            db,
            checked,
            name=name,
            starts_at=starts_at,
            duration_minutes=duration_minutes,
            capacity=capacity,
            resource_key=resource_key,
        )
    except (ValueError, BusinessRuleError) as exc:
        db.rollback()
        return redirect("/admin", error=str(exc))
    return redirect("/admin", message=f"สร้าง Slot {slot.name} แล้ว")


@router.get("/portal/admin/documents/{document_id}")
def admin_download_document(
    document_id: str,
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> Response:
    checked = current_user_or_redirect(user, admin=True)
    if isinstance(checked, RedirectResponse):
        return checked
    from app.models import RegistrationDocument

    document = db.get(RegistrationDocument, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="ไม่พบเอกสาร")
    path = Path(document.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์ใน Storage")
    return FileResponse(path, filename=document.original_name, media_type=document.mime_type)
