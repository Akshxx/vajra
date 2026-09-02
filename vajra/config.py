from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://vajra:vajra@localhost:5432/vajra"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_MAX_CONNECTIONS: int = 50

    # ClickHouse (analytics)
    CLICKHOUSE_HOST: str = "localhost"
    CLICKHOUSE_PORT: int = 9000
    CLICKHOUSE_USER: str = "default"
    CLICKHOUSE_PASSWORD: str = ""
    CLICKHOUSE_DATABASE: str = "vajra_analytics"

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_CONSUMER_GROUP: str = "vajra"
    KAFKA_TOPICS: dict = {
        "transactions": "vajra.transactions",
        "chargebacks": "vajra.chargebacks",
        "fraud_alerts": "vajra.fraud_alerts",
        "audit_events": "vajra.audit_events",
    }

    # Razorpay (test mode)
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

    # LLM
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_TOKENS: int = 4096

    # Groq (Free tier, very fast LPU, Llama-3.1-70B)
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.1-70b-versatile"

    # Ollama (local LLM - free, unlimited)
    OLLAMA_ENABLED: bool = False
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "llama3.1:8b"

    # Embeddings
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_DIM: int = 384

    # Tribunal
    TRIBUNAL_CONFIDENCE_THRESHOLD: float = 0.7
    TRIBUNAL_MAX_ROUNDS: int = 3
    TRIBUNAL_CITATION_REQUIRED: bool = True

    # Fraud Sentinel
    FRAUD_DETECTION_WINDOW_MINUTES: int = 60
    FRAUD_MIN_CLUSTER_SIZE: int = 3
    FRAUD_SIMILARITY_THRESHOLD: float = 0.85

    # Policy Synthesis
    POLICY_MAX_EXECUTION_TIME_SECONDS: int = 300
    POLICY_MAX_RETRIES: int = 3

    # Audit
    AUDIT_MERKLE_TREE_DEPTH: int = 20
    AUDIT_RETENTION_DAYS: int = 2555  # 7 years

    # Eval
    EVAL_HARNESS_ENABLED: bool = True
    EVAL_CI_THRESHOLDS: dict = {
        "chargeback_win_rate": 0.60,
        "false_positive_cost_per_10k": 1500,
        "fraud_precision_at_80_recall": 0.85,
        "fraud_detection_latency_seconds": 300,
    }

    # Observability
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"
    OTEL_SERVICE_NAME: str = "vajra"
    PROMETHEUS_PORT: int = 9090


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
