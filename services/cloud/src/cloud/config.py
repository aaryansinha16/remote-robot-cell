from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    site_id: str = "site-001"

    broker_host: str = "localhost"
    broker_port: int = 1883
    broker_username: str = "cloud-api"
    broker_password: str = "cloud-dev-secret"
    client_id: str = "cloud-api"
    session_expiry_s: int = 7 * 24 * 3600

    db_path: Path = Path("/data/cloud.db")

    heartbeat_timeout_s: float = 15.0
    retry_base_s: float = 2.0
    retry_max_s: float = 60.0
    default_job_ttl_s: int = 1800

    log_level: str = "INFO"
