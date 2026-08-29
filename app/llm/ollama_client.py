"""Minimal Ollama HTTP client."""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import get_settings


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(
        self,
        host: str | None = None,
        timeout_sec: int | None = None,
    ) -> None:
        settings = get_settings()
        self.host = (host or settings.ollama.host).rstrip("/")
        self.timeout = timeout_sec or settings.ollama.timeout_sec

    def is_available(self) -> bool:
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.get(f"{self.host}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(f"{self.host}/api/tags")
                r.raise_for_status()
                data = r.json()
                return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except Exception as exc:
            raise OllamaError(f"Failed to list Ollama models: {exc}") from exc

    def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        format_json: bool = False,
    ) -> str:
        settings = get_settings()
        temp = settings.ollama.temperature if temperature is None else temperature
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temp},
        }
        if system:
            payload["system"] = system
        if format_json:
            payload["format"] = "json"

        try:
            with httpx.Client(timeout=float(self.timeout)) as client:
                r = client.post(f"{self.host}/api/generate", json=payload)
                r.raise_for_status()
                data = r.json()
                return (data.get("response") or "").strip()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama generate failed: {exc}") from exc

    def generate_json(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        raw = self.generate(
            model,
            prompt,
            system=system,
            temperature=temperature,
            format_json=True,
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Best-effort extract JSON object
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                return json.loads(raw[start : end + 1])
            raise OllamaError(f"Model did not return valid JSON: {raw[:200]}")
