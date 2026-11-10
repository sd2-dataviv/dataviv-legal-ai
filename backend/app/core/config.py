"""Application settings, read from environment variables (prefix DATAVIV_) or backend/.env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATAVIV_", env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore"
    )

    env: Literal["dev", "test", "staging", "prod"] = "dev"
    app_name: str = "Dataviv AI OS"

    # The API connects as app_user (RLS applies). Migrations and seeding use the owner URL.
    database_url: str = "postgresql+asyncpg://app_user:app_user_dev_only@localhost:5432/dataviv"
    database_owner_url: str = "postgresql+asyncpg://dataviv:dataviv_dev_only@localhost:5432/dataviv"

    # Auth. Dev/test: HS256 tokens signed with jwt_secret. Staging/prod: OIDC (Keycloak) via JWKS.
    jwt_secret: str = "change-me-dev-only-secret-32-bytes-min"  # noqa: S105 - dev default, overridden in prod
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 60 * 8
    oidc_issuer: str | None = None
    oidc_audience: str | None = None

    # WorkflowHub, the hosting platform, sets these when it starts the app (they do not exist locally).
    # Other apps and AI agents call the shared API (node.yaml `api:`) with a signed X-Wfh-User header;
    # scripts may send the caller's platform token as X-Auth-Token, checked with the platform's auth service.
    wfh_app_api_secret: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("WFH_APP_API_SECRET", "DATAVIV_WFH_APP_API_SECRET")
    )
    wfh_app_api_audience: str | None = Field(
        default=None, validation_alias=AliasChoices("WFH_APP_API_AUDIENCE", "DATAVIV_WFH_APP_API_AUDIENCE")
    )
    wfh_auth_service_url: str | None = Field(
        default=None, validation_alias=AliasChoices("WFH_AUTH_SERVICE_URL", "DATAVIV_WFH_AUTH_SERVICE_URL")
    )
    # Platform callers act as the Dataviv user with the same e-mail. In dev/test only, a caller with no
    # Dataviv account acts as this user instead (a demo convenience); unset, or outside dev/test = refused.
    platform_fallback_email: str | None = None

    @field_validator(
        "wfh_app_api_secret",
        "wfh_app_api_audience",
        "wfh_auth_service_url",
        "platform_fallback_email",
        mode="before",
    )
    @classmethod
    def _platform_blank_is_unset(cls, v: object) -> object:
        return None if isinstance(v, str) and not v.strip() else v

    # Model gateway (FND-06, ADR-012). "fake" = deterministic offline model (tests, no key);
    # "groq" = Groq's OpenAI-compatible API. The key is read from GROQ_API_KEY (no DATAVIV_ prefix).
    # "ollama" = a local Ollama server (ollama_url); set llm_default_model to its model, e.g. gpt-oss:20b.
    llm_backend: Literal["fake", "groq", "ollama"] = "fake"
    ollama_url: str = "http://127.0.0.1:11434"
    # Context window per request (prompt + output). Ollama's own default is too small for prompts with
    # passages; keep it the same on every call, as changing it makes Ollama reload the model.
    ollama_num_ctx: int = 16384
    ollama_keep_alive: str = "30m"  # keep the model loaded between calls
    groq_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("GROQ_API_KEY", "DATAVIV_GROQ_API_KEY")
    )
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_default_model: str = "openai/gpt-oss-120b"
    llm_models: list[str] = ["openai/gpt-oss-120b"]  # catalogue a firm policy may choose from
    # USD per million tokens (input, output), used for the cost shown on each answer
    llm_prices_per_million: dict[str, tuple[float, float]] = {"openai/gpt-oss-120b": (0.15, 0.60)}
    llm_reasoning_effort: Literal["low", "medium", "high"] = "low"
    llm_max_output_tokens: int = 2048
    llm_timeout_seconds: float = 90.0
    llm_max_wait_seconds: float = 30.0  # wait up to this long for a rate-limit reset, then give up

    # Assistant (Step 2). Sized for Groq's 8K tokens-per-minute developer limit.
    assistant_top_k: int = 6
    assistant_chunk_chars: int = 1500
    assistant_context_chars: int = 9000
    assistant_history_turns: int = 2
    assistant_verifier: Literal["lexical", "model"] = "lexical"  # "model" adds one extra model call

    # Review tables (Step 3): one model call per document; jobs yield to uploads every few documents.
    review_context_chars: int = 7000
    review_docs_per_slice: int = 5

    cors_origins: list[str] = ["http://localhost:3000"]

    # Vault (Step 1). Originals are stored content-addressed under storage_dir (ADR-010).
    storage_dir: Path = Path(__file__).resolve().parents[3] / "var" / "storage"
    max_upload_mb: int = 50
    max_files_per_upload: int = 500
    # Dataviv OCR service; unset = scanned pages are kept as "OCR pending" (document status "partial").
    ocr_url: str | None = None
    ocr_languages: str = "eng+hin"
    ocr_timeout_seconds: float = 120.0

    # Semantic search (Step 1c, ADR-019). Both an embedder and a vector store must be set to turn it on;
    # with either at "none", search is keyword-only exactly as in Step 1.
    #   embeddings_backend: none | fake (tests) | http (TEI or any OpenAI-compatible /embeddings) | local
    #                       | ollama (native /api/embed on ollama_url, or embeddings_url if set; model "bge-m3")
    # Changing embeddings_model starts a new vector collection: run scripts/reindex_vectors.py afterwards.
    embeddings_backend: Literal["none", "fake", "http", "local", "ollama"] = "none"
    embeddings_model: str = "BAAI/bge-m3"
    embeddings_dim: int = 1024
    embeddings_url: str | None = None  # http backend, e.g. http://gpu-vm:8080/v1
    embeddings_api_key: SecretStr | None = None
    embeddings_batch: int = 16
    embeddings_max_tokens: int = 512  # passages are <= ~1,500 characters; longer inputs are cut
    embeddings_device: str | None = None  # local backend: "cpu", "cuda"; None = best available
    # Load the local model when the API starts (in the background), not on the first search
    embeddings_preload: bool = True
    embeddings_timeout_seconds: float = 60.0
    vector_store: Literal["none", "memory", "qdrant"] = "none"
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: SecretStr | None = None
    vector_collection_prefix: str = "dataviv_chunks"
    # Passages found ONLY by meaning must be at least this similar (cosine) to the query, so a question
    # with no good answer still gets no passages and the Assistant can say so. Tuned for bge-m3.
    semantic_min_score: float = 0.5
    search_candidates: int = 40  # per ranking (keyword, semantic) before fusion
    search_rrf_k: int = 60  # reciprocal rank fusion constant

    @property
    def semantic_enabled(self) -> bool:
        return self.embeddings_backend != "none" and self.vector_store != "none"

    # E-signature (Step 2e, ADR-021). none = off; fake = offline test mode (no e-mail is sent, signing is
    # simulated from the console); docusign = DocuSign eSignature REST API with the JWT grant.
    esign_backend: Literal["none", "fake", "docusign"] = "none"
    # DocuSign app ("integration key"), the user the app acts as, and that user's RSA private key (PEM).
    docusign_integration_key: str | None = None
    docusign_user_id: str | None = None
    docusign_private_key: SecretStr | None = None  # the PEM text itself, or ...
    docusign_private_key_path: Path | None = None  # ... a file holding it
    docusign_account_id: str | None = None  # None = the user's default account
    # account-d.docusign.com = developer sandbox (demo); account.docusign.com = production
    docusign_auth_server: str = "account-d.docusign.com"
    # Public HTTPS address of POST /v1/esign/docusign/connect for status callbacks; None = poll only
    docusign_webhook_url: str | None = None
    # Connect HMAC key; callbacks without a valid signature are refused (required outside dev/test)
    docusign_connect_secret: SecretStr | None = None

    @field_validator(
        "docusign_integration_key",
        "docusign_user_id",
        "docusign_private_key",
        "docusign_private_key_path",
        "docusign_account_id",
        "docusign_webhook_url",
        "docusign_connect_secret",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """An empty value (e.g. passed through docker compose as ${VAR:-}) means "not set"."""
        return None if isinstance(v, str) and not v.strip() else v

    esign_timeout_seconds: float = 30.0
    esign_poll_seconds: float = 60.0  # how often open envelopes are checked when no callback arrives

    # Background worker (ADR-009). In dev the API runs one worker loop in-process; in production run
    # `python -m app.worker` as its own service and set this to false.
    run_worker_in_api: bool = True
    worker_poll_seconds: float = 1.0

    @property
    def dev_auth_enabled(self) -> bool:
        return self.env in ("dev", "test")


@lru_cache
def get_settings() -> Settings:
    return Settings()
