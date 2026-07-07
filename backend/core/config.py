"""
Application configuration powered by `pydantic-settings`.

All runtime configuration is sourced from environment variables (loaded from the
local `.env` file during development). Using a strongly-typed Settings object
means a missing or malformed value fails fast at startup rather than surfacing
as an obscure error deep inside a request handler.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Type-safe application settings.

    Environment variable names are matched case-insensitively, so `LLM_MODEL`
    in the environment maps onto the `llm_model` attribute below.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- Application metadata -------------------------------------------------
    app_name: str = "Enterprise Document Intelligence Platform"
    app_version: str = "1.0.0"
    environment: str = "local"

    # ---- LLM / extraction -----------------------------------------------------
    # Provider is selected at runtime with NO code change. The "openai_compatible"
    # provider is the air-gapped path (points at an internal vLLM/Ollama/gateway);
    # "gemini" / "anthropic" use the native vendor SDKs with your own keys.
    llm_provider: str = "gemini"  # emergent | gemini | anthropic | openai_compatible
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    # When true (or when the selected provider has no key/endpoint) the pipeline
    # falls back to a deterministic mock extractor so the demo always works.
    use_mock_llm: bool = False

    # Emergent universal-key gateway (one key for OpenAI/Anthropic/Gemini models).
    # Used by the hosted preview; locally you can use your own vendor keys instead.
    emergent_llm_key: str = ""
    emergent_model_provider: str = "openai"  # openai | anthropic | gemini
    emergent_model: str = "gpt-5.4"

    # Native Google Gemini (own Google AI Studio key) — local-testing path.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3-pro-preview"
    gemini_base_url: str = ""  # optional internal proxy override

    # Native Anthropic Claude (own key) — local-testing path.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_base_url: str = ""  # optional internal proxy override

    # OpenAI-compatible endpoint — AIR-GAPPED path (vLLM / Ollama / TGI / LiteLLM).
    openai_api_key: str = "not-needed"  # SDK requires a value even if server ignores it
    openai_base_url: str = "http://localhost:11434/v1"
    openai_model: str = "llama3.1"
    openai_json_mode: bool = True  # set False if your server rejects response_format

    # ---- Object storage (S3) --------------------------------------------------
    # The demo stubs out S3 access so reviewers do not need AWS credentials.
    use_mock_s3: bool = True

    # ---- Authentication (mock OIDC / JWT) ------------------------------------
    jwt_secret: str = "local-dev-mock-secret"
    jwt_algorithm: str = "HS256"
    jwt_audience: str = "doc-intel"
    jwt_issuer: str = "mock-oidc"
    # Escape hatch for local smoke-tests only; never enable in production.
    auth_disabled: bool = False

    # ---- Persistence (async job tracking) ------------------------------------
    database_url: str = "sqlite+aiosqlite:///./jobs.db"

    # ---- Pipeline tuning ------------------------------------------------------
    max_sync_file_mb: int = 5
    chunk_char_threshold: int = 12000
    chunk_size: int = 8000
    chunk_overlap: int = 400  # chars carried into the next chunk for context

    # ---- OCR (offline / air-gapped) — PaddleOCR (PP-OCRv5) via ONNXRuntime ----
    ocr_enabled: bool = True
    # PaddleOCR recognition model / script. "latin" is the multilingual Latin
    # model (German + English + ~35 more Latin-script languages, incl. ä ö ü ß).
    ocr_languages: str = "latin"
    # Optional override for the directory holding the PP-OCRv5 rec model + dict.
    # Empty -> backend/models/ocr (bundled with the app).
    ocr_model_dir: str = ""
    ocr_dpi: int = 300  # rasterization DPI for scanned PDFs
    ocr_max_pages: int = 25  # cap pages OCR'd on large scanned PDFs (latency/memory guard)
    # Use a vision-capable LLM (via the configured provider) to read images
    # directly instead of PaddleOCR. Dramatically more accurate on ID cards /
    # licences with security-pattern backgrounds. Falls back to PaddleOCR when
    # the provider has no vision support or the vision call fails.
    ocr_vision: bool = True

    # ---- Platform-provided (unused here but present in .env) ------------------
    mongo_url: str = ""
    db_name: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (parsed once per process)."""
    return Settings()
