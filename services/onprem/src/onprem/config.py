from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    site_id: str = "site-001"

    cloud_broker_host: str = "localhost"
    cloud_broker_port: int = 1883
    cloud_broker_password: str = "site-dev-secret"
    session_expiry_s: int = 7 * 24 * 3600

    site_broker_host: str = "localhost"
    site_broker_port: int = 1884

    db_path: Path = Path("/data/onprem.db")
    site_map_path: Path = Path("/config/site.yaml")

    max_queue: int = 10
    heartbeat_interval_s: float = 5.0
    robot_heartbeat_timeout_s: float = 3.0
    drain_timeout_s: float = 12.0

    failure_rate: float = 0.15
    sim_seed: int | None = None
    arm_speed_mps: float = 1.5
    grip_s: float = 0.8
    max_job_runtime_s: float = 30.0

    safety_classes: list[str] = ["person"]
    safety_resume_after_s: float = 2.0
    safety_feed_timeout_s: float = 1.0

    log_level: str = "INFO"
