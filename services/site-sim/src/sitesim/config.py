from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    site_broker_host: str = "localhost"
    site_broker_port: int = 1884
    site_map_path: Path = Path("/config/site.yaml")
    rate_hz: float = 5.0
    step_hz: float = 20.0
    view_hz: float = 20.0
    telemetry_timeout_s: float = 1.5
    ambient_traffic: bool = True
    log_level: str = "INFO"
