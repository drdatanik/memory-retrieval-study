"""Fixed settings for the study. Changing anything here after the pilot is a deviation (log it in DESIGN.md)."""

# --- Models, all open-weight, served through OpenRouter (OpenAI-compatible API) -------------
# Each role is pinned to a named provider endpoint so quantisation and behaviour don't drift between runs.
# Provider tags, context sizes and prices were read from OpenRouter's /models and /models/{id}/endpoints
# on 2026-10-12.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_KEY_ENV = "OPENROUTER_API_KEY"
REQUEST_TIMEOUT_S = 900          # an ~800k-token prompt can take minutes
MAX_RETRIES = 8                  # transient failures (upstream 429, 5xx, timeouts); llm.chat retries, not the SDK

# OpenRouter's free endpoints (model IDs ending ":free") cost $0 but allow 20 requests/minute and 1,000
# requests/day per account (50/day until the account has bought at least $10 of credit). llm.chat holds
# requests to free models to this rate; paid models aren't limited.
FREE_REQUESTS_PER_MINUTE = 18
REQUESTS_PER_MINUTE = FREE_REQUESTS_PER_MINUTE   # alias used by llm.make_client

# --- The model under test. Pick one before the pilot; changing it afterwards is a deviation. --------
SUBJECT = "deepseek-v4.1-flash"

SUBJECTS = {
    "nemotron-3-ultra-free": {
        # 1,000,000-token context, tools, reasoning effort, $0. (thinkingmachines/inkling:free was the first
        # choice, but its free endpoint only serves apps on OpenRouter's list: 403.)
        "role": {
            "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "provider": {"only": ["nvidia"], "allow_fallbacks": False, "require_parameters": True},
            "temperature": 0,
            "reasoning": {"effort": "medium"},
            "max_tokens": 16000,
        },
        # Free, so cost is modelled with the same model's paid endpoint card (USD per million tokens).
        "price_card": {"input": 0.60, "output": 2.40, "cache_read": 0.12, "cache_write": 0.60},
        "tokenizer_repo": "nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16",
    },
    "deepseek-v4.1-flash": {
        # Paid (about $5 for the study), 1,048,576-token context, automatic prefix caching (checked live
        # 2026-10-13: a repeated 40,509-token prompt read 40,448 tokens from cache). Pinned to DeepInfra
        # (fp8): DeepSeek's own endpoint may train on prompts, which the account's privacy settings exclude.
        "role": {
            "model": "deepseek/deepseek-v4.1-flash",
            "provider": {"only": ["deepinfra/fp8"], "allow_fallbacks": False, "require_parameters": True},
            "temperature": 0,
            "reasoning": {"effort": "medium"},
            "max_tokens": 16000,
        },
        # DeepInfra rates, read 2026-10-13; cache writes are billed at the normal input price.
        "price_card": {"input": 0.14, "output": 0.42, "cache_read": 0.0042, "cache_write": 0.14},
        "tokenizer_repo": "deepseek-ai/DeepSeek-V4.1-Flash",
    },
}

# --- Grader and distractor writer: fixed to NVIDIA Nemotron 3 Super, free, whatever the subject. ---
_NEMOTRON_SUPER_FREE = {"model": "nvidia/nemotron-3-super-120b-a12b:free",
                        "provider": {"only": ["nvidia"], "allow_fallbacks": False, "require_parameters": True}}
# Modelled card from the paid endpoint of the same model (no cache price is listed, so cache = input).
_NEMOTRON_SUPER_CARD = {"input": 0.08, "output": 0.45, "cache_read": 0.08, "cache_write": 0.08}

ROLES = {
    "subject": SUBJECTS[SUBJECT]["role"],
    "judge": {**_NEMOTRON_SUPER_FREE, "temperature": 0, "reasoning": {"effort": "medium"}, "max_tokens": 8000},
    # (google/gemma-4-31b-it:free was the first choice for the generator, but it has no json_schema output: 404.)
    # Reasoning capped at 2,000 tokens: uncapped it sometimes ran to max_tokens with no output; switched off,
    # it ignored the requested memory count. Capped, 3 of 3 test patients were valid (2026-10-13).
    "generator": {**_NEMOTRON_SUPER_FREE, "temperature": 0.7, "reasoning": {"max_tokens": 2000}, "max_tokens": 12000},
}
PRICE_CARDS = {
    "subject": SUBJECTS[SUBJECT]["price_card"],
    "judge": _NEMOTRON_SUPER_CARD,
    "generator": _NEMOTRON_SUPER_CARD,
}
# Local tokenizer of the subject model, for sizing store levels without API calls.
SUBJECT_TOKENIZER_REPO = SUBJECTS[SUBJECT]["tokenizer_repo"]


def is_free(role: str) -> bool:
    """True when the role's model runs on an OpenRouter free endpoint."""
    return ROLES[role]["model"].endswith(":free")


# Compatibility aliases (older modules import these names).
SUBJECT_MODEL = ROLES["subject"]["model"]
JUDGE_MODEL = ROLES["judge"]["model"]
GENERATOR_MODEL = ROLES["generator"]["model"]
MAX_TOKENS = ROLES["subject"]["max_tokens"]
EFFORT = ROLES["subject"]["reasoning"]["effort"]
JUDGE_EFFORT = ROLES["judge"]["reasoning"]["effort"]

# --- Retrieval and agent loop ----------------------------------------------------------------
EMBED_MODEL = "BAAI/bge-base-en-v1.5"
MAX_TOOL_CALLS = 8
MAX_TURNS = 12
SEARCH_K = 8

# --- World -----------------------------------------------------------------------------------
TODAY = "2026-10-12"
WINDOW_START = "2025-04-01"
WINDOW_END = "2026-10-11"
CORE_DOCTORS = ("D01", "D02")
DISTRACTOR_DOCTORS = ("D03", "D04", "D05", "D06")

# Target size of the CTX system prompt per level, in subject-model tokens (L1 = core only, ~41k).
LEVEL_TARGETS = {"L1": None, "L2": 100_000, "L3": 200_000, "L4": 400_000, "L5": 800_000}
