"""Chat LLM used for research, lesson structuring and card writing (all optional)."""
from __future__ import annotations

import json
import re

import httpx

from .config import Settings


class LLMUnavailable(RuntimeError):
    """No LLM backend configured (LLM_BACKEND=none)."""


class NoLLM:
    available = False

    def chat(self, system: str, user: str, max_tokens: int = 3000) -> str:
        raise LLMUnavailable("No LLM configured. Set LLM_BACKEND=databricks or bedrock.")


class DatabricksLLM:
    available = True

    def __init__(self, host: str, token: str, endpoint: str, client: httpx.Client | None = None):
        self.endpoint = endpoint
        self._client = client or httpx.Client(
            base_url=host, headers={"Authorization": f"Bearer {token}"}, timeout=120.0
        )

    def chat(self, system, user, max_tokens=3000):
        r = self._client.post(
            f"/serving-endpoints/{self.endpoint}/invocations",
            json={"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                  "max_tokens": max_tokens, "temperature": 0.2},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class BedrockLLM:
    available = True

    def __init__(self, region: str, model_id: str, client=None):
        import boto3
        self.model_id = model_id
        self._client = client or boto3.client("bedrock-runtime", region_name=region)

    def chat(self, system, user, max_tokens=3000):
        resp = self._client.converse(
            modelId=self.model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0.2},
        )
        return resp["output"]["message"]["content"][0]["text"]


def build_llm(s: Settings):
    if s.llm_backend == "databricks":
        return DatabricksLLM(s.databricks_host, s.databricks_token, s.llm_endpoint)
    if s.llm_backend == "bedrock":
        return BedrockLLM(s.aws_region, s.bedrock_llm_model)
    return NoLLM()


def extract_json(text: str):
    """Pull the first JSON object/array out of an LLM reply (tolerates code fences and prose)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    spans = [(text.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if text.find(o) != -1]
    for start, _, closer in sorted(spans):  # whichever bracket opens first is the outer value
        end = text.rfind(closer)
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("LLM did not return valid JSON")
