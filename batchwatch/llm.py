"""Nebius Token Factory client for NVIDIA Nemotron models."""
import os
import threading
from dataclasses import dataclass, field

from openai import OpenAI

BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

# API model id and price in USD per 1M tokens (input, output), from the Token Factory catalog, Oct 2026.
MODELS = {
    "nano": ("nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B", 0.06, 0.24),
    "lightning": ("nvidia/Nemotron-3_5-Lightning", 0.06, 0.24),
    "super": ("nvidia/nemotron-3-super-120b-a12b", 0.30, 0.90),
    "ultra": ("nvidia/Nemotron-3-Ultra-550b-a55b", 1.00, 3.00),
}
PRICES = {model_id: (p_in, p_out) for model_id, p_in, p_out in MODELS.values()}


def out_of_credit(error) -> bool:
    """Token Factory answers 402 Payment Required when the account balance is used up."""
    text = str(error or "")
    return "402" in text and ("Payment Required" in text or "exhaust" in text.lower())


def make_client() -> OpenAI:
    key = os.environ.get("NEBIUS_API_KEY")
    if not key:
        raise SystemExit("NEBIUS_API_KEY is not set. Copy .env.example to .env and paste your key there.")
    # Nemotron reasons before answering, so long alerts can take a while; retry dropped connections.
    return OpenAI(base_url=BASE_URL, api_key=key, timeout=300, max_retries=4)


@dataclass
class UsageMeter:
    """Adds up tokens and cost across calls (thread-safe), for the README and the feedback form."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    calls_by_model: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, usage, model_id: str) -> None:
        if usage is None:
            return
        p_in, p_out = PRICES.get(model_id, (0.0, 0.0))
        with self._lock:
            self.calls += 1
            self.calls_by_model[model_id] = self.calls_by_model.get(model_id, 0) + 1
            self.prompt_tokens += usage.prompt_tokens or 0
            self.completion_tokens += usage.completion_tokens or 0
            self.cost_usd += ((usage.prompt_tokens or 0) * p_in + (usage.completion_tokens or 0) * p_out) / 1_000_000
