"""Thin wrapper around the official `anthropic` Python SDK.

Every call is cached to storage/responses/{request_id}.json so the whole
run can be replayed later without an API key (required for submission).
Uses the SAME cache file format as this project's original implementation,
so already-paid-for real model responses are reused as-is -- no new API
calls are needed just because the calling code changed language.

mode is recorded as one of:
  - "real"   : a live API response, cached after the fact
  - "cached" : replayed from a previously saved real response
  - "mock"   : a deterministic local stand-in, used with --mock and never
               confused with a real model call
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import anthropic

from orderintake.ai.mock_extraction import mock_respond


class ClaudeClient:
    def __init__(self, api_key: str, model: str, responses_dir: str, mock: bool = False) -> None:
        self._api_key = api_key
        self._model = model
        self._responses_dir = responses_dir
        self._mock = mock
        os.makedirs(responses_dir, exist_ok=True)

    def _cache_path(self, request_id: str) -> str:
        return os.path.join(self._responses_dir, f"{request_id}.json")

    def complete(self, request_id: str, system: str, user_message: str) -> dict:
        cache_path = self._cache_path(request_id)

        if os.path.isfile(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                cached = json.load(f)
            cached["mode"] = "mock" if cached.get("mode") == "mock" else "cached"
            return cached

        if self._mock:
            result = {
                "mode": "mock",
                "model": "mock-local-stub",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "text": mock_respond(user_message),
            }
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
            return result

        if not self._api_key:
            raise RuntimeError(
                f"No ANTHROPIC_API_KEY set and no cached response for {request_id}. "
                "Either set ANTHROPIC_API_KEY in .env, or run with --mock, or restore storage/responses/."
            )

        client = anthropic.Anthropic(api_key=self._api_key)
        response = client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        text = response.content[0].text if response.content else ""

        result = {
            "mode": "real",
            "model": self._model,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "text": text,
            "raw_response": response.model_dump(mode="json"),
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

        return result
