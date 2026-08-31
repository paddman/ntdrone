from __future__ import annotations

import os
import shutil
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:////tmp/ntdrone-pytest.db"
os.environ["STORAGE_ROOT"] = "/tmp/ntdrone-pytest-data"
os.environ["SECRET_KEY"] = "pytest-secret-key-that-is-long-and-stable-for-tests"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "Admin-Test-123!"
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["VPN_DRIVER"] = "mock"
os.environ["SIMULATION_DRIVER"] = "mock"
os.environ["MOCK_SIMULATION_DURATION_SECONDS"] = "1"
os.environ["WORKER_POLL_SECONDS"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.main import app
from app.services import seed_admin


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    storage = Path(os.environ["STORAGE_ROOT"])
    shutil.rmtree(storage, ignore_errors=True)
    storage.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        seed_admin(db, get_settings())
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
