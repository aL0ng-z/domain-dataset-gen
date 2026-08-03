from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "datasetgen"
    postgres_user: str = "datasetgen"
    postgres_password: str = "datasetgen_dev_password"

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin123"
    minio_bucket_documents: str = "documents"
    minio_bucket_outputs: str = "outputs"
    # 新对象的可选统一 key 前缀；生产留空，测试使用 tests/{run_id}/。
    minio_key_prefix: str = ""
    minio_secure: bool = False

    # JWT
    jwt_secret_key: str = "dev-secret-key-change-in-production"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7
    jwt_algorithm: str = "HS256"

    # Remote document parsers (kept server-side; never store in ParserProfile JSON)
    mineru_api_token: SecretStr | None = None
    paddleocr_api_token: SecretStr | None = None

    # T03: 服务端 ParserEndpointRegistry。JSON 数组：
    # [{"endpoint_ref": "...", "parser_name": "...", "network_zone": "public-remote",
    #   "base_url": "https://...", "credential_ref": "env:MINERU_API_TOKEN",
    #   "credential_origins": ["https://mineru.net"],
    #   "artifact_origins": [{"usage": "upload", "suffix": "*.oss-cn-*.aliyuncs.com"}],
    #   "redirect_policy": "deny", ...}]
    # 解析器真正使用的 URL/Token 全部由此 registry 派生，ParserProfile 只存 endpoint_ref。
    parser_endpoint_registry: str = "[]"
    # managed-local 服务的 admin 精确地址清单（URL -> 内部请求），供本地服务解析器使用。
    parser_managed_local_urls: dict[str, str] = {}

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:3003",
        "http://localhost:3004",
        "http://localhost:3005",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:3003",
        "http://127.0.0.1:3004",
        "http://127.0.0.1:3005",
    ]

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
