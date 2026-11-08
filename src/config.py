"""Central configuration (README section 4: model name, dormancy days, thresholds, paths).

Values come from environment variables or a local `.env` file. Secrets are held as
`SecretStr` so they are masked in repr/str and never end up in logs by accident.
Switching from the mock to the real Pixxi API is a configuration change only:
set PIXXI_MODE=real, PIXXI_BASE_URL=https://dataapi.pixxicrm.ae and PIXXI_TOKEN.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PIXXI_REAL_BASE_URL = "https://dataapi.pixxicrm.ae"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Pixxi (test bed: mock until real access exists; see docs/decisions.md) ---
    pixxi_mode: Literal["mock", "real"] = "mock"
    pixxi_base_url: str = "http://127.0.0.1:8001"
    pixxi_token: SecretStr | None = None  # sent as X-PIXXI-TOKEN
    pixxi_app_name: str = "PixxiAgents"  # {AppName} path segment on addLog
    pixxi_timeout_seconds: float = 15.0
    # Real API: at least 2 s between read requests (writes are exempt). 0 disables.
    pixxi_min_read_interval: float = 2.0
    pixxi_page_size: int = Field(default=100, ge=1, le=500)
    pixxi_max_retries_on_429: int = 3
    # Pixxi timestamps carry no timezone; assumed Gulf Standard Time (unverified).
    pixxi_timezone: str = "Asia/Dubai"

    # --- Webhook receiver (C3) ---
    webhook_secret: SecretStr | None = None  # required as ?key=... on callback URLs
    webhook_public_base_url: str = "http://127.0.0.1:8002"
    # UPDATE_LEADS on a lead we wrote to within this many seconds is treated as self-caused.
    self_write_window_seconds: int = 120

    # --- LLM (C7). Optional until an agent needs it. ---
    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_temperature: float = 0.0

    # --- Domain thresholds ---
    dormancy_days: int = 30
    budget_tolerance: float = 0.10  # +/- 10 percent, Section 3.12

    # --- Paths ---
    data_dir: Path = PROJECT_ROOT / "data"
    state_db: Path = PROJECT_ROOT / "data" / "middleware.sqlite"  # write ledger, webhook dedup
    audit_log: Path = PROJECT_ROOT / "data" / "audit" / "writes.jsonl"
    checkpoint_db: Path = PROJECT_ROOT / "data" / "checkpoints.sqlite"
    eval_runs_dir: Path = PROJECT_ROOT / "eval" / "runs"


@lru_cache
def get_settings() -> Settings:
    return Settings()
