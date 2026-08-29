"""
LLM Client for GridMind Specialist narrative synthesis.
Calls OpenRouter/OpenAI-compatible endpoints to synthesize operator findings
and recommendations from deterministic telemetry and constraint evaluation.
Falls back to deterministic templates under logged [DEGRADED_MODE].
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("gridmind.llm")


class LLMClient:
    """
    Client for synthesizing natural language operator findings and recommendations.
    Uses OpenAI/OpenRouter-compatible chat completions.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = 10.0,
        max_retries: int = 2,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("TRUEFORGE_API_KEY")
        )
        self.base_url = (
            base_url
            or os.environ.get("LLM_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://openrouter.ai/api/v1"
        ).rstrip("/")
        self.model = (
            model
            or os.environ.get("LLM_MODEL")
            or os.environ.get("OPENROUTER_MODEL")
            or "openai/gpt-4o-mini"
        )
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
        If the model call fails or no API key is configured, triggers the [DEGRADED_MODE]
        fallback and returns (default_finding, default_recommendation).
        """
        if not self.api_key:
            logger.warning(
                "[DEGRADED_MODE] No LLM API key configured (OPENROUTER_API_KEY / OPENAI_API_KEY). "
                "Falling back to deterministic template synthesis for %s.",
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
            "response_format": {"type": "json_object"},
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
                    content = data["choices"][0]["message"]["content"]
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
