"""C7 LLM service: the only place the middleware talks to a language model.

- Provider and model come from config (LLM_PROVIDER, LLM_MODEL); agents never name a model.
- Prompts live in versioned files under src/llm/prompts/ (README section 10), never in code.
- Every call returns a validated pydantic object (structured output) and writes one row to
  eval/runs/llm_calls.jsonl with run_id, scenario_id, model, prompt version, temperature,
  tokens, estimated cost and latency (README section 9). Failed calls are logged too.
- Spend guard: after LLM_MAX_CALLS_PER_PROCESS calls the service refuses further calls.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, TypeVar

from pydantic import BaseModel

from src.config import Settings, get_settings

PROMPTS_DIR = Path(__file__).parent / "prompts"
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """The model call failed or its answer did not match the expected schema."""


class LLMBudgetError(LLMError):
    """The per-process call limit (LLM_MAX_CALLS_PER_PROCESS) has been reached."""


# -- prompts -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Prompt:
    """A versioned prompt file: front matter (id, version) plus '## system' and '## user'."""

    id: str
    version: int
    system: str
    user: str

    @property
    def ref(self) -> str:
        return f"{self.id}/v{self.version}"

    def render(self, **variables: Any) -> tuple[str, str]:
        """Fill $placeholders. Raises KeyError if a variable is missing."""
        values = {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1)
                  for k, v in variables.items()}
        return Template(self.system).substitute(values), Template(self.user).substitute(values)


_SECTION_RE = re.compile(r"^## (system|user)\s*$", re.MULTILINE)


def load_prompt(name: str, prompts_dir: Path = PROMPTS_DIR) -> Prompt:
    """Load src/llm/prompts/<name>.md."""
    text = (prompts_dir / f"{name}.md").read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise ValueError(f"prompt {name}: missing front matter")
    _, front, body = text.split("---", 2)
    meta = dict(line.split(":", 1) for line in front.strip().splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    parts = _SECTION_RE.split(body)
    sections = {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    if set(sections) != {"system", "user"}:
        raise ValueError(f"prompt {name}: needs exactly one '## system' and one '## user' section")
    return Prompt(id=meta["id"], version=int(meta["version"]), system=sections["system"], user=sections["user"])


# -- call record ---------------------------------------------------------------------------

class LLMCallRecord(BaseModel):
    timestamp: datetime
    run_id: str
    scenario_id: str | None
    agent_id: str
    prompt: str  # "<id>/v<version>"
    provider: str
    model: str
    temperature: float
    max_tokens: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_s: float
    ok: bool
    error: str | None = None


# -- service -------------------------------------------------------------------------------

class LLMService:
    def __init__(self, settings: Settings | None = None, chat_model: Any = None, log_path: Path | None = None):
        self.settings = settings or get_settings()
        self._chat_model = chat_model
        self.log_path = log_path if log_path is not None else self.settings.eval_runs_dir / "llm_calls.jsonl"
        self.calls_made = 0
        self.records: list[LLMCallRecord] = []
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self.settings.llm_model

    def _model(self):
        if self._chat_model is None:
            s = self.settings
            key = s.llm_api_key
            if key is None or not key.get_secret_value().strip():
                env = "OPENAI_API_KEY" if s.llm_provider == "openai" else "ANTHROPIC_API_KEY"
                raise LLMError(f"{env} is not set in .env")
            common = dict(model=s.llm_model, api_key=key, temperature=s.llm_temperature,
                          max_tokens=s.llm_max_tokens, timeout=60, max_retries=2)
            if s.llm_provider == "openai":
                from langchain_openai import ChatOpenAI

                self._chat_model = ChatOpenAI(**common)
            else:
                from langchain_anthropic import ChatAnthropic

                self._chat_model = ChatAnthropic(**common)
        return self._chat_model

    def _reserve_call(self) -> None:
        with self._lock:
            if self.calls_made >= self.settings.llm_max_calls_per_process:
                raise LLMBudgetError(
                    f"LLM call limit reached ({self.settings.llm_max_calls_per_process} per process); "
                    "raise LLM_MAX_CALLS_PER_PROCESS to allow more"
                )
            self.calls_made += 1

    def _cost(self, tokens_in: int | None, tokens_out: int | None) -> float | None:
        p_in, p_out = self.settings.llm_price_input_per_mtok, self.settings.llm_price_output_per_mtok
        if None in (tokens_in, tokens_out, p_in, p_out):
            return None
        return round((tokens_in * p_in + tokens_out * p_out) / 1_000_000, 6)

    def structured(
        self,
        prompt: Prompt,
        variables: dict[str, Any],
        schema: type[T],
        *,
        run_id: str,
        agent_id: str,
        scenario_id: str | None = None,
    ) -> tuple[T, LLMCallRecord]:
        """Run one prompt and return the answer parsed into `schema`, plus its call record."""
        system, user = prompt.render(**variables)
        self._reserve_call()
        runnable = self._model().with_structured_output(schema, method="function_calling", include_raw=True)
        start = time.perf_counter()
        error: str | None = None
        parsed: T | None = None
        tokens_in = tokens_out = None
        try:
            out = runnable.invoke([("system", system), ("user", user)])
            usage = getattr(out.get("raw"), "usage_metadata", None) or {}
            tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
            parsed = out.get("parsed")
            if out.get("parsing_error") is not None or parsed is None:
                error = f"answer did not match {schema.__name__}: {out.get('parsing_error')}"
        except Exception as exc:  # provider/network errors: log, then raise as LLMError
            error = f"{type(exc).__name__}: {exc}"
        record = LLMCallRecord(
            timestamp=datetime.now(timezone.utc),
            run_id=run_id,
            scenario_id=scenario_id,
            agent_id=agent_id,
            prompt=prompt.ref,
            provider=self.settings.llm_provider,
            model=self.settings.llm_model,
            temperature=self.settings.llm_temperature,
            max_tokens=self.settings.llm_max_tokens,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cost_usd=self._cost(tokens_in, tokens_out),
            latency_s=round(time.perf_counter() - start, 3),
            ok=error is None,
            error=error,
        )
        self._log(record)
        if error is not None:
            raise LLMError(error)
        return parsed, record

    def _log(self, record: LLMCallRecord) -> None:
        with self._lock:
            self.records.append(record)
            if self.log_path is None:
                return
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(record.model_dump_json() + "\n")

    def total_cost(self) -> float:
        return round(sum(r.cost_usd or 0 for r in self.records), 6)
