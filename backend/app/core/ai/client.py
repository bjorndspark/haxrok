"""The entire AI integration surface: one AsyncOpenAI client pointed at
whatever OpenAI-compatible endpoint AI_BASE_URL names (Meta's API, a gateway
such as LiteLLM or Bifrost, or api.openai.com when empty). Provider routing,
keys, budgets, and fallbacks live behind that endpoint — this repo only
forwards the optional, gateway-only AI_CHAT_FALLBACKS list verbatim via
fallbacks_extra_body().

Features call the helpers in app.core.ai (chat.complete_text,
structured.complete_structured); they never construct SDK clients
themselves.
"""

from functools import lru_cache

from openai import AsyncOpenAI

from app.core.config import settings


@lru_cache
def get_ai_client() -> AsyncOpenAI:
    # Gateways typically accept any key; "unused" keeps the SDK happy when
    # AI_API_KEY is empty. Hitting OpenAI directly without a key fails at
    # request time, which is the honest error.
    # AI_TIMEOUT_S replaces the SDK's 10-minute default: a hung model
    # call fails the request instead of pinning it. httpx applies it per
    # operation (connect, each read), so long streams aren't cut off.
    return AsyncOpenAI(
        base_url=settings.AI_BASE_URL or None,
        api_key=settings.AI_API_KEY or "unused",
        timeout=settings.AI_TIMEOUT_S,
    )


def fallbacks_extra_body(raw: str) -> dict[str, list[str]] | None:
    """Build the gateway ``fallbacks`` request field (Bifrost's format) from a
    comma-separated ``provider/model`` list. Returns None when unset so nothing
    extra is sent (required when calling Meta's API directly) and a gateway's
    own routing fallbacks still apply."""
    chain = [m.strip() for m in raw.split(",") if m.strip()]
    return {"fallbacks": chain} if chain else None
