"""
Pluggable, self-hostable LLM providers.

The provider is selected at runtime via ``LLM_PROVIDER`` (gemini | anthropic |
openai_compatible | emergent). Each provider talks DIRECTLY to a vendor SDK
using the operator's own API key / endpoint — the ``openai_compatible`` provider
can run fully air-gapped against a self-hosted vLLM / Ollama / TGI server or an
internal corporate gateway (e.g. LiteLLM). The ``emergent`` provider routes
through the Emergent universal-key gateway (OpenAI / Anthropic / Gemini models
with a single key) — used by the hosted preview.

Each provider exposes:
  * ``complete(system, prompt) -> str``                      (text extraction)
  * ``complete_vision(system, prompt, image_b64, mime) -> str``  (image input)

JSON parsing / validation is handled by the caller (LLMExtractionService).
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger("llm")


class LLMProvider(ABC):
    """Provider-agnostic async chat interface."""

    # Overridden to True by providers that can accept image input.
    supports_vision: bool = False

    @abstractmethod
    async def complete(self, system: str, prompt: str) -> str:
        """Return the model's raw text response (expected to be JSON)."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Identifier of the underlying model (for logging / responses)."""

    async def complete_vision(
        self, system: str, prompt: str, image_b64: str, mime: str
    ) -> str:
        """Transcribe/answer about an image. Override in vision-capable providers."""
        raise NotImplementedError("This provider does not support vision input.")


# ---------------------------------------------------------------------------
# Google Gemini (native, own AI Studio key) — local-testing path
# ---------------------------------------------------------------------------
class GeminiProvider(LLMProvider):
    supports_vision = True  # all current Gemini models accept image parts

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float,
        base_url: Optional[str] = None,
    ) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._model = model
        self._temperature = temperature
        http_options = types.HttpOptions(base_url=base_url) if base_url else None
        self._client = genai.Client(api_key=api_key, http_options=http_options)

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(self, system: str, prompt: str) -> str:
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=self._types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",  # native JSON enforcement
                temperature=self._temperature,
            ),
        )
        return response.text or ""

    async def complete_vision(
        self, system: str, prompt: str, image_b64: str, mime: str
    ) -> str:
        import base64

        image_part = self._types.Part.from_bytes(
            data=base64.b64decode(image_b64), mime_type=mime
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[image_part, prompt],
            config=self._types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                temperature=self._temperature,
            ),
        )
        return response.text or ""


# ---------------------------------------------------------------------------
# Anthropic Claude (native, own key) — local-testing path
# ---------------------------------------------------------------------------
class AnthropicProvider(LLMProvider):
    supports_vision = True  # Claude 3+ models accept base64 image blocks

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float,
        max_tokens: int,
        base_url: Optional[str] = None,
    ) -> None:
        from anthropic import AsyncAnthropic

        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url or None)

    @property
    def model_name(self) -> str:
        return self._model

    @staticmethod
    def _join_text(message) -> str:
        return "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )

    async def complete(self, system: str, prompt: str) -> str:
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            system=system,  # Claude takes the system prompt as a top-level field
            messages=[{"role": "user", "content": prompt}],
        )
        return self._join_text(message)

    async def complete_vision(
        self, system: str, prompt: str, image_b64: str, mime: str
    ) -> str:
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        return self._join_text(message)


# ---------------------------------------------------------------------------
# OpenAI-compatible (vLLM / Ollama / TGI / LiteLLM / corporate gateway)
# This is the AIR-GAPPED path — base_url points at an internal endpoint.
# ---------------------------------------------------------------------------
class OpenAICompatibleProvider(LLMProvider):
    # OpenAI-compatible vision models (llava, Qwen2-VL, Llama-3.2-Vision, ...)
    # accept the standard image_url message part.
    supports_vision = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float,
        json_mode: bool = True,
    ) -> None:
        from openai import AsyncOpenAI

        self._model = model
        self._temperature = temperature
        self._json_mode = json_mode
        # api_key is required by the SDK even when the server ignores it.
        self._client = AsyncOpenAI(api_key=api_key or "not-needed", base_url=base_url)

    @property
    def model_name(self) -> str:
        return self._model

    async def complete(self, system: str, prompt: str) -> str:
        kwargs: dict = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self._temperature,
        }
        if self._json_mode:
            # Best-effort native JSON mode (supported by vLLM/Ollama/OpenAI).
            kwargs["response_format"] = {"type": "json_object"}
        response = await self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    async def complete_vision(
        self, system: str, prompt: str, image_b64: str, mime: str
    ) -> str:
        # Standard OpenAI multimodal message format (works with llava / Qwen2-VL
        # / Llama-3.2-Vision served behind an OpenAI-compatible endpoint).
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_b64}"},
                        },
                    ],
                },
            ],
            temperature=self._temperature,
        )
        return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Emergent universal-key gateway (OpenAI / Anthropic / Gemini, one key)
# Used by the hosted preview so no vendor key is required.
# ---------------------------------------------------------------------------
class EmergentProvider(LLMProvider):
    supports_vision = True  # gpt-5.x / claude / gemini behind the gateway are multimodal

    def __init__(self, *, api_key: str, provider: str, model: str) -> None:
        self._api_key = api_key
        self._provider = provider
        self._model = model

    @property
    def model_name(self) -> str:
        return f"{self._provider}:{self._model}"

    def _chat(self, system: str):
        from emergentintegrations.llm.chat import LlmChat

        # A fresh, stateless chat per extraction call (no history bleed-through).
        return LlmChat(
            api_key=self._api_key,
            session_id=f"extract-{uuid.uuid4().hex}",
            system_message=system,
        ).with_model(self._provider, self._model)

    async def complete(self, system: str, prompt: str) -> str:
        from emergentintegrations.llm.chat import UserMessage

        response = await self._chat(system).send_message(UserMessage(text=prompt))
        return str(response or "")

    async def complete_vision(
        self, system: str, prompt: str, image_b64: str, mime: str
    ) -> str:
        from emergentintegrations.llm.chat import ImageContent, UserMessage

        message = UserMessage(
            text=prompt,
            file_contents=[ImageContent(image_base64=image_b64)],
        )
        response = await self._chat(system).send_message(message)
        return str(response or "")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def build_provider(settings) -> Optional[LLMProvider]:
    """Instantiate the configured provider, or return None to use the mock.

    Returning ``None`` (rather than raising) when a cloud provider's key is
    missing keeps the demo functional out-of-the-box: the pipeline transparently
    falls back to the deterministic mock extractor.
    """
    provider = (settings.llm_provider or "").lower()

    if provider == "emergent":
        if not settings.emergent_llm_key:
            logger.warning("LLM_PROVIDER=emergent but EMERGENT_LLM_KEY is empty -> using mock.")
            return None
        return EmergentProvider(
            api_key=settings.emergent_llm_key,
            provider=settings.emergent_model_provider,
            model=settings.emergent_model,
        )

    if provider == "gemini":
        if not settings.gemini_api_key:
            logger.warning("LLM_PROVIDER=gemini but GEMINI_API_KEY is empty -> using mock.")
            return None
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            temperature=settings.llm_temperature,
            base_url=settings.gemini_base_url or None,
        )

    if provider == "anthropic":
        if not settings.anthropic_api_key:
            logger.warning("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty -> using mock.")
            return None
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            base_url=settings.anthropic_base_url or None,
        )

    if provider == "openai_compatible":
        if not settings.openai_base_url:
            logger.warning("LLM_PROVIDER=openai_compatible but OPENAI_BASE_URL is empty -> mock.")
            return None
        return OpenAICompatibleProvider(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            model=settings.openai_model,
            temperature=settings.llm_temperature,
            json_mode=settings.openai_json_mode,
        )

    raise ValueError(
        f"Unsupported LLM_PROVIDER '{settings.llm_provider}'. "
        "Use one of: emergent | gemini | anthropic | openai_compatible."
    )
