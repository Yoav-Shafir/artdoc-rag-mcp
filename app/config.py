"""Configuration for Web Platform RAG application.

Settings are read from environment variables, falling back to the project's
.env file, falling back to the defaults below. Each group reads variables with
its own prefix, e.g. ChromaSettings.port <- CHROMA_PORT.
"""

from pathlib import Path
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


def _settings_config(prefix: str) -> SettingsConfigDict:
    # extra="ignore": .env holds variables for every group (and for Docker),
    # so each group must skip the ones that aren't its own.
    return SettingsConfigDict(env_prefix=prefix, env_file=ENV_FILE, extra="ignore")


class S3Settings(BaseSettings):
    """S3/MinIO settings (S3_*)."""

    model_config = _settings_config("S3_")

    endpoint_url: str = "http://localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin"
    region: str = "eu-west-2"
    bucket: str = "overstone-rag-documents"


class ChromaSettings(BaseSettings):
    """ChromaDB settings (CHROMA_*)."""

    model_config = _settings_config("CHROMA_")

    host: str = "localhost"
    port: int = 8000
    collection: str = "overstone-assets"


class EmbeddingsSettings(BaseSettings):
    """Embedding model settings (EMBEDDINGS_*)."""

    model_config = _settings_config("EMBEDDINGS_")

    model: str = "amazon.titan-embed-text-v2:0"
    dimension: int = 1024
    normalize: bool = True


class ChunkingSettings(BaseSettings):
    """Text chunking settings (CHUNK_*). Sizes are in characters."""

    model_config = _settings_config("CHUNK_")

    size: int = Field(default=1000, gt=0)
    overlap: int = Field(default=150, ge=0)

    @model_validator(mode="after")
    def _overlap_smaller_than_size(self) -> "ChunkingSettings":
        if self.overlap >= self.size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.overlap}) must be less than CHUNK_SIZE ({self.size})"
            )
        return self


class BedrockSettings(BaseSettings):
    """AWS Bedrock settings (BEDROCK_*).

    Bedrock uses real AWS credentials from the default credential chain
    (AWS_PROFILE / ~/.aws), never the MinIO S3_* keys.
    """

    model_config = _settings_config("BEDROCK_")

    region: str = "eu-west-2"
    # AWS_PROFILE has no BEDROCK_ prefix; the alias reads it by its standard name.
    aws_profile: str | None = Field(default=None, validation_alias="AWS_PROFILE")
    # Required from Phase 8 (generation) onwards; see plan step 8.1.
    llm_model_id: str | None = None
    # The LLM can live in another region than the embeddings: e.g. `eu.` inference
    # profiles aren't offered in eu-west-2, so the LLM is called from eu-west-1.
    llm_region: str = "eu-west-1"
    llm_max_tokens: int = Field(default=1024, gt=0)


class DatabaseSettings(BaseSettings):
    """Platform database (RDS_*): the MySQL `file` and `asset` tables.

    Uses the same variable names as web-platform-server-v2. Read-only access.
    """

    model_config = _settings_config("RDS_")

    hostname: str = "localhost"
    port: int = 3306
    username: str = "root"
    password: str = ""
    db_name: str = "overstone_v2"


class RetrievalSettings(BaseSettings):
    """Retrieval settings (RETRIEVAL_*)."""

    model_config = _settings_config("RETRIEVAL_")

    top_k: int = Field(default=5, gt=0)
    min_score: float = 0.0


class AppSettings(BaseModel):
    """Main application settings, one sub-group per concern.

    A plain model, not BaseSettings: it reads nothing itself, each group does.
    """

    # No `= Field(default_factory=...)` defaults here: linters such as Pylint
    # read those literally and think each group is a FieldInfo. The groups are
    # built explicitly in from_env() instead.
    s3: S3Settings
    chroma: ChromaSettings
    embeddings: EmbeddingsSettings
    chunking: ChunkingSettings
    bedrock: BedrockSettings
    db: DatabaseSettings
    retrieval: RetrievalSettings
    project_root: Path = PROJECT_ROOT

    @classmethod
    def from_env(cls) -> "AppSettings":
        """Build every settings group from environment variables / .env."""
        return cls(
            s3=S3Settings(),
            chroma=ChromaSettings(),
            embeddings=EmbeddingsSettings(),
            chunking=ChunkingSettings(),
            bedrock=BedrockSettings(),
            db=DatabaseSettings(),
            retrieval=RetrievalSettings(),
        )


# Global config instance.
# Built once, on the first `import app.config`; Python caches modules, so every
# later import reuses this same object - a singleton in practice.
config = AppSettings.from_env()
