from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables or ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "NT Drone Competition Portal"
    app_env: str = "development"
    debug: bool = False
    base_url: str = "http://localhost:8000"
    timezone: str = "Asia/Bangkok"

    secret_key: str = "replace-this-development-secret"
    database_url: str = "sqlite:///./data/ntdrone.db"
    storage_root: Path = Path("./data")
    max_upload_bytes: int = 10 * 1024 * 1024

    session_cookie_name: str = "ntdrone_session"
    challenge_cookie_name: str = "ntdrone_challenge"
    csrf_cookie_name: str = "ntdrone_csrf"
    session_ttl_seconds: int = 8 * 60 * 60
    challenge_ttl_seconds: int = 10 * 60
    cookie_secure: bool = False

    admin_username: str = "admin"
    admin_password: str = "ChangeMe-NTDrone-2026!"
    admin_email: str = "admin@example.local"

    slot_default_duration_minutes: int = 180
    booking_cooldown_seconds: int = 300
    worker_poll_seconds: int = 15

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "NT Drone <noreply@example.local>"
    smtp_starttls: bool = True
    smtp_timeout_seconds: int = 10

    sms_webhook_url: str | None = None
    sms_webhook_token: str | None = None

    vpn_driver: str = "mock"
    vpn_client_network: str = "10.248.0.0/24"
    wireguard_interface: str = "wg-ntdrone"
    wireguard_server_endpoint: str = "vpn.example.local:51820"
    wireguard_server_public_key: str = "REPLACE_WITH_SERVER_PUBLIC_KEY"
    wireguard_dns: str = "1.1.1.1"
    vpn_enable_script: Path = Path("./app/wireguard/enable_peer.sh")
    vpn_disable_script: Path = Path("./app/wireguard/disable_peer.sh")

    simulation_driver: str = "mock"
    simulator_api_url: str | None = None
    simulator_api_token: str = "replace-simulator-token"
    mock_simulation_duration_seconds: int = 20

    seed_demo_data: bool = False

    @field_validator("vpn_driver")
    @classmethod
    def validate_vpn_driver(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"mock", "wireguard"}:
            raise ValueError("VPN_DRIVER must be 'mock' or 'wireguard'")
        return value

    @field_validator("simulation_driver")
    @classmethod
    def validate_simulation_driver(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"mock", "http"}:
            raise ValueError("SIMULATION_DRIVER must be 'mock' or 'http'")
        return value

    @field_validator("storage_root", "vpn_enable_script", "vpn_disable_script", mode="before")
    @classmethod
    def expand_paths(cls, value: str | Path) -> Path:
        return Path(value).expanduser()

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
