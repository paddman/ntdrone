from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import Settings, get_settings
from app.db import get_db
from app.dependencies import get_current_user, require_simulator_token
from app.models import Booking, SimulationRun, User, UserRole
from app.schemas import QueueNextResponse, SimulationCreate, SimulationResultUpdate, SimulationRunSummary
from app.services import (
    BusinessRuleError,
    claim_next_blind_run,
    control_simulation_run,
    create_simulation_run,
    from_json,
    start_simulation_run,
    update_simulation_result,
)

router = APIRouter(prefix="/api/v1", tags=["Simulation"])


@router.get("/simulations", response_model=list[SimulationRunSummary])
def list_runs(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[SimulationRun]:
    query = select(SimulationRun).order_by(SimulationRun.created_at.desc())
    if user.role != UserRole.ADMIN.value:
        if not user.team_id:
            return []
        query = query.where(SimulationRun.team_id == user.team_id)
    return list(db.scalars(query).all())


@router.post("/simulations", response_model=SimulationRunSummary, status_code=201)
def create_run(
    payload: SimulationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SimulationRun:
    booking = db.scalar(select(Booking).options(joinedload(Booking.slot)).where(Booking.id == payload.booking_id))
    if not booking:
        raise HTTPException(status_code=404, detail="ไม่พบ Booking")
    try:
        return create_simulation_run(
            db,
            user,
            booking,
            mode=payload.mode,
            scenario_name=payload.scenario_name,
            config=payload.config,
        )
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/simulations/{run_id}/start", response_model=SimulationRunSummary)
def start_run(
    run_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SimulationRun:
    run = db.get(SimulationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="ไม่พบ Simulation")
    try:
        return start_simulation_run(db, user, run)
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/simulations/{run_id}/{action}", response_model=SimulationRunSummary)
def control_run(
    run_id: str,
    action: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SimulationRun:
    run = db.get(SimulationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="ไม่พบ Simulation")
    try:
        return control_simulation_run(db, user, run, action)
    except BusinessRuleError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post(
    "/simulator/queue/next",
    response_model=QueueNextResponse,
    dependencies=[Depends(require_simulator_token)],
)
def simulator_queue_next(
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> QueueNextResponse:
    run = claim_next_blind_run(db, settings, dispatch=False)
    if not run:
        return QueueNextResponse(process="end")
    return QueueNextResponse(
        process="exe",
        run_id=run.id,
        team_id=run.team_id,
        booking_id=run.booking_id,
        scenario_name=run.scenario_name,
        config=from_json(run.config_json),
    )


@router.post(
    "/simulator/runs/{run_id}/result",
    response_model=SimulationRunSummary,
    dependencies=[Depends(require_simulator_token)],
)
def simulator_result(
    run_id: str,
    payload: SimulationResultUpdate,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SimulationRun:
    run = db.get(SimulationRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="ไม่พบ Simulation")
    try:
        return update_simulation_result(
            db,
            run,
            status_value=payload.status,
            metrics=payload.metrics,
            result=payload.result,
            output_path=payload.output_path,
            remote_run_id=payload.remote_run_id,
            settings=settings,
        )
    except BusinessRuleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
