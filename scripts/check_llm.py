"""Smoke test: is the LLM key in .env valid? Makes ONE tiny call (a few tokens).

Usage:  uv run python -m scripts.check_llm
Never prints the key.
"""

from __future__ import annotations

import sys
import time

from src.config import get_settings


def main() -> int:
    s = get_settings()
    key = s.llm_api_key
    print(f"Provider: {s.llm_provider}   Model: {s.llm_model}")
    if key is None or not key.get_secret_value().strip():
        env_var = "OPENAI_API_KEY" if s.llm_provider == "openai" else "ANTHROPIC_API_KEY"
        print(f"FAIL: {env_var} is empty in .env")
        return 1

    if s.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=s.llm_model, api_key=key, temperature=0, max_tokens=5)
    else:
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(model=s.llm_model, api_key=key, temperature=0, max_tokens=5)

    start = time.perf_counter()
    try:
        reply = llm.invoke("Reply with exactly: OK")
    except Exception as exc:  # show the provider's error type and message, not the key
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1
    elapsed = time.perf_counter() - start

    usage = getattr(reply, "usage_metadata", None) or {}
    print(f"Reply: {reply.content!r}")
    print(f"Tokens: {usage.get('input_tokens', '?')} in / {usage.get('output_tokens', '?')} out   "
          f"Latency: {elapsed:.2f} s")
    print("OK: the key works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
