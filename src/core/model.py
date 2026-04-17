from google.adk.models import Gemini
from google.genai import types as genai_types

from ..core.config import settings

# Configuration with full Exponential Backoff and Jitter
retry_config = genai_types.HttpRetryOptions(
    attempts=settings.GEMINI_RETRY_ATTEMPTS,
    initial_delay=settings.GEMINI_RETRY_INITIAL_DELAY,
    max_delay=settings.GEMINI_RETRY_MAX_DELAY,
    exp_base=settings.GEMINI_RETRY_EXP_BASE,
    jitter=settings.GEMINI_RETRY_JITTER,
    http_status_codes=settings.GEMINI_RETRY_STATUS_CODES,
)

llm = Gemini(
    model=settings.GEMINI_MODEL,
    http_retry_options=retry_config,
    generate_content_config=genai_types.GenerateContentConfig(
        thinking_config=genai_types.ThinkingConfig(
            include_thoughts=True,
        )
    ),
)
