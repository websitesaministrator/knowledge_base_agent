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

    # Logging
    log_level: str = Field("INFO", env="LOG_LEVEL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


# Singleton — import `settings` everywhere
settings = Settings()
