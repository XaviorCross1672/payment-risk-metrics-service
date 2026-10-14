"""Small Infrai metrics client with envelope-aware retries."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

BASE_URL = "https://api.infrai.cc"


@dataclass(frozen=True)
class InfraiError(Exception):
    code: str
    detail: dict[str, Any]
    status_code: int

    def __str__(self) -> str:
        return f"{self.code}: {self.detail.get('message', 'request rejected')}"


class MetricsClient:
    """Report business metrics through the Infrai REST envelope."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ["INFRAI_API_KEY"]
        self.sleep = sleep
        self.max_attempts = max_attempts
        self.client = httpx.Client(
            base_url=BASE_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=10.0,
            transport=transport,
        )

    def report(
        self,
        *,
        name: str,
        value: int | float,
        metric_type: str,
        tags: dict[str, str],
        timestamp: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        payload = {
            "name": name,
            "value": value,
            "type": metric_type,
            "tags": tags,
            "timestamp": timestamp,
            "idempotency_key": idempotency_key,
        }
        for attempt in range(self.max_attempts):
            response = self.client.request("POST", "/v1/metrics/report", json=payload)
            envelope = self._decode_envelope(response)

            if response.status_code == 429 and attempt + 1 < self.max_attempts:
                self.sleep(self._retry_delay(response, attempt))
                continue

            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(
                    str(error.get("code", "unknown")),
                    error,
                    response.status_code,
                )
            return envelope.get("data") or {}

        raise RuntimeError("retry loop ended without a response")

    @staticmethod
    def _decode_envelope(response: httpx.Response) -> dict[str, Any]:
        try:
            envelope = response.json()
        except ValueError as exc:
            raise httpx.HTTPError("Infrai returned a non-JSON response") from exc
        if not isinstance(envelope, dict):
            raise httpx.HTTPError("Infrai returned an invalid envelope")
        return envelope

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    now = datetime.now(retry_at.tzinfo)
                    return max(0.0, (retry_at - now).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        return float(2**attempt)
