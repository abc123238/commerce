"""应用配置：从环境变量 / .env 加载，类型安全，按域分组。

所有字段由同名大写环境变量覆盖（如 postgres_host -> POSTGRES_HOST）。
"""
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 应用 ----
    app_name: str = "ai-commerce-platform"
    env: str = "dev"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # ---- 安全 ----
    # 无默认值：未配置 JWT_SECRET_KEY 时启动即报错，杜绝用公开默认值伪造全部 token。
    jwt_secret_key: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 30
    jwt_refresh_expire_days: int = 7
    # 生产环境（HTTPS）必须设为 true，否则 token cookie 可被明文传输窃听
    cookie_secure: bool = False

    # ---- 数据库 ----
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_user: str = "commerce"
    postgres_password: str = "commerce"
    postgres_db: str = "commerce"

    # ---- Redis ----
    redis_url: str = "redis://127.0.0.1:6379/0"

    # ---- LLM ----
    anthropic_base_url: str | None = None
    anthropic_auth_token: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    llm_timeout_seconds: float = 60.0

    # ---- RAG ----
    qdrant_url: str = "http://127.0.0.1:6333"
    embedding_model: str = "jinaai/jina-embeddings-v2-base-zh"
    rag_top_k: int = 6
    rag_collection: str = "knowledge_base"
    # HuggingFace 下载镜像（国内网络不可直连 hf.co 时的适配项）
    hf_endpoint: str | None = None
    hf_disable_xet: bool = False

    # ---- 可观测（Langfuse 可插拔）----
    langfuse_enabled: bool = False
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "http://127.0.0.1:3000"

    # ---- 限流（次/分钟/键）----
    rate_limit_public: int = 100
    rate_limit_chat: int = 20

    @model_validator(mode="after")
    def _guard_prod_debug(self) -> "Settings":
        # 生产开启 DEBUG 会泄露堆栈/SQL，属于高危配置；测试环境用 test 明确豁免。
        if self.env not in ("dev", "test", "local") and self.debug:
            raise ValueError("生产环境禁止开启 DEBUG=true")
        return self

    @property
    def database_url(self) -> str:
        """异步 SQLAlchemy 连接串（asyncpg 驱动）"""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
