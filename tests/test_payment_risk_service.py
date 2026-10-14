import json
from datetime import datetime, timezone

import httpx

from src.infrai_metrics import MetricsClient
from src.payment_risk_service import PaymentEvent, PaymentStatus, RiskAction, process_payment


def test_high_risk_payment_is_blocked_and_metrics_are_idempotent() -> None:
    requests: list[httpx.Request] = []

    def accept(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "data": {"accepted": True}, "error": None, "metadata": {}})

    metrics = MetricsClient(api_key="test-key", transport=httpx.MockTransport(accept))
    event = PaymentEvent(
        event_id="evt_9001",
        account_id="acct_42",
        amount_minor=250000,
        currency="EUR",
        status=PaymentStatus.AUTHORIZED,
        risk_score=91,
        occurred_at=datetime(2026, 9, 5, 10, 30, tzinfo=timezone.utc),
    )

    result = process_payment(event, metrics)

    assert result.action is RiskAction.BLOCK
    assert result.notification.audit_reference == "payment:evt_9001"
    assert result.metrics_reported == 2
    bodies = [json.loads(request.content) for request in requests]
    assert [body["type"] for body in bodies] == ["counter", "gauge"]
    assert [body["idempotency_key"] for body in bodies] == [
        "evt_9001:payment-events:v1",
        "evt_9001:risk-score:v1",
    ]
    assert all("account_id" not in body["tags"] for body in bodies)


def test_rate_limit_honors_retry_after() -> None:
    delays: list[float] = []
    calls = 0

    def rate_limit_once(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "3"},
                json={"ok": False, "data": None, "error": {"message": "retry later"}, "metadata": {}},
            )
        return httpx.Response(200, json={"ok": True, "data": {"accepted": True}, "error": None, "metadata": {}})

    metrics = MetricsClient(
        api_key="test-key",
        transport=httpx.MockTransport(rate_limit_once),
        sleep=delays.append,
    )
    metrics.report(
        name="fintech.payment.events",
        value=1,
        metric_type="counter",
        tags={"status": "authorized"},
        timestamp="2026-09-05T10:30:00+00:00",
        idempotency_key="evt_retry:payment-events:v1",
    )

    assert calls == 2
    assert delays == [3.0]
