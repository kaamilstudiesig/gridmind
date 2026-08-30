"""
LLM Client for GridMind Specialist narrative synthesis.
Calls OpenRouter/OpenAI-compatible endpoints to synthesize operator findings
and recommendations from deterministic telemetry and constraint evaluation.
Falls back to deterministic templates under logged [DEGRADED_MODE] ONLY upon
caught API/network failures after retries.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("gridmind.llm")


class LLMClient:
    """
    Client for synthesizing natural language operator findings and recommendations.
    Uses OpenAI/OpenRouter-compatible chat completions via standard environment variables.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 12.0,
        max_retries: int = 2,
    ) -> None:
        # Resolve provider credentials, base URL, and default model
        explicit_key = api_key
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")
        openai_key = os.environ.get("OPENAI_API_KEY")
        trueforge_key = os.environ.get("TRUEFORGE_API_KEY")

        if explicit_key:
            self.api_key = explicit_key
            self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
            self.model = model or os.environ.get("LLM_MODEL") or "openrouter/free"
        elif openrouter_key:
            self.api_key = openrouter_key
            self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
            self.model = model or os.environ.get("LLM_MODEL") or os.environ.get("OPENROUTER_MODEL") or "openrouter/free"
        elif openai_key:
            self.api_key = openai_key
            self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
            self.model = model or os.environ.get("LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
        elif trueforge_key:
            self.api_key = trueforge_key
            tf_base = base_url or os.environ.get("LLM_BASE_URL") or os.environ.get("TRUEFORGE_BASE_URL")
            if not tf_base:
                raise ValueError(
                    "TRUEFORGE_API_KEY is configured but LLM_BASE_URL (or TRUEFORGE_BASE_URL) is missing. "
                    "TrueForge requires an explicit proxy base URL."
                )
            self.base_url = tf_base.rstrip("/")
            self.model = model or os.environ.get("LLM_MODEL") or os.environ.get("TRUEFORGE_MODEL") or "default"
        else:
            self.api_key = None
            self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
            self.model = model or os.environ.get("LLM_MODEL") or "openrouter/free"

        self.timeout = timeout
        self.max_retries = max_retries

    def generate_narrative(
        self,
        agent_role: str,
        status: str,
        candidates: list[dict[str, Any]],
        evidence: list[Any],
        risks: list[str],
        default_finding: str,
        default_recommendation: str,
    ) -> tuple[str, str]:
        """
        Synthesizes (finding, recommendation) for a specialist.
        Emits [DEGRADED_MODE] and falls back to deterministic templates if API key
        is missing or upon caught network/API failures after retries.
        """
        if not self.api_key:
            logger.warning(
                "[DEGRADED_MODE] Missing LLM API key or configuration. Falling back to deterministic template synthesis for %s.",
                agent_role,
            )
            return default_finding, default_recommendation

        prompt = (
            f"You are the GridMind {agent_role.upper()} Specialist on an electrical distribution grid.\n"
            f"Given the deterministic telemetry, status, and constraints below, synthesize a concise operator 'finding' "
            f"and 'recommendation'. Return ONLY a JSON object with keys 'finding' and 'recommendation'.\n\n"
            f"Agent Role: {agent_role}\n"
            f"Status: {status}\n"
            f"Candidates: {json.dumps(candidates, ensure_ascii=False)}\n"
            f"Evidence: {json.dumps(evidence, ensure_ascii=False)}\n"
            f"Identified Risks: {json.dumps(risks, ensure_ascii=False)}\n"
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an expert power systems engineering assistant. "
                        "Respond strictly in JSON format: {\"finding\": \"...\", \"recommendation\": \"...\"}"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }

        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    content = data["choices"][0]["message"]["content"].strip()
                    # Strip possible markdown code fences (e.g. ```json ... ```)
                    if "```" in content:
                        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
                        if match:
                            content = match.group(1)
                    parsed = json.loads(content)
                    finding = str(parsed.get("finding", default_finding)).strip()
                    recommendation = str(parsed.get("recommendation", default_recommendation)).strip()
                    if finding and recommendation:
                        return finding, recommendation
            except Exception as err:
                last_err = err
                logger.debug("LLM call attempt %d failed: %s", attempt, err)

        logger.warning(
            "[DEGRADED_MODE] LLM call failed after %d retries: %s. Falling back to deterministic template synthesis for %s.",
            self.max_retries,
            last_err,
            agent_role,
        )
        return default_finding, default_recommendation
