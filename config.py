"""
Shared configuration loaded from environment variables.
All agents and tools import settings from here — never hard-code values.
"""
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # LLM
    openai_api_key: str = Field(..., env="OPENAI_API_KEY")
    llm_provider: str = Field("openai", env="LLM_PROVIDER")
    llm_model: str = Field("gpt-4o", env="LLM_MODEL")

    # Storage
    kb_data_path: str = Field("data/ps03_support_tickets.csv", env="KB_DATA_PATH")
    chroma_persist_dir: str = Field("chroma_db", env="CHROMA_PERSIST_DIR")

    # Agent behaviour guards
    agent_max_iterations: int = Field(10, env="AGENT_MAX_ITERATIONS")
    agent_retry_attempts: int = Field(3, env="AGENT_RETRY_ATTEMPTS")

    # Cluster scoring weights
    weight_volume: float = Field(0.3, env="WEIGHT_VOLUME")
    weight_repetition: float = Field(0.3, env="WEIGHT_REPETITION")
    weight_fix_inconsistency: float = Field(0.2, env="WEIGHT_FIX_INCONSISTENCY")
    weight_severity: float = Field(0.2, env="WEIGHT_SEVERITY")

    # Lifecycle Monitor thresholds
    lifecycle_new_fix_share_threshold: float = Field(0.30, env="LIFECYCLE_NEW_FIX_SHARE_THRESHOLD")
    lifecycle_rep_rate_threshold: float = Field(0.60, env="LIFECYCLE_REP_RATE_THRESHOLD")
    lifecycle_avg_res_increase_threshold: float = Field(0.20, env="LIFECYCLE_AVG_RES_INCREASE_THRESHOLD")
    lifecycle_retire_ticket_threshold: int = Field(2, env="LIFECYCLE_RETIRE_TICKET_THRESHOLD")

    # Logging
    log_level: str = Field("INFO", env="LOG_LEVEL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# Singleton — import `settings` everywhere
settings = Settings()
