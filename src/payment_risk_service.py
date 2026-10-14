"""Typed payment decision endpoint and runnable example."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from src.infrai_metrics import InfraiError, MetricsClient


class PaymentStatus(str, Enum):
    AUTHORIZED = "authorized"
    DECLINED = "declined"
    REFUNDED = "refunded"


class RiskAction(str, Enum):
    APPROVE = "approve"
    MANUAL_REVIEW = "manual_review"
    BLOCK = "block"


class PaymentEvent(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    account_id: str = Field(min_length=1, max_length=100)
    amount_minor: int = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    status: PaymentStatus
    risk_score: Annotated[int, Field(ge=0, le=100)]
    occurred_at: datetime


class AuditNotification(BaseModel):
    audit_reference: str
    category: str
    summary: str


class PaymentDecision(BaseModel):
    event_id: str
    action: RiskAction
    notification: AuditNotification
    metrics_reported: int


def choose_action(event: PaymentEvent) -> RiskAction:
    if event.status == PaymentStatus.DECLINED or event.risk_score >= 85:
        return RiskAction.BLOCK
    if event.risk_score >= 60:
        return RiskAction.MANUAL_REVIEW
    return RiskAction.APPROVE


def build_notification(event: PaymentEvent, action: RiskAction) -> AuditNotification:
    return AuditNotification(
        audit_reference=f"payment:{event.event_id}",
        category=f"payment_{action.value}",
        summary=(
            f"{event.status.value} {event.currency} payment; "
            f"risk score {event.risk_score}; action {action.value}"
        ),
    )


def process_payment(event: PaymentEvent, metrics: MetricsClient) -> PaymentDecision:
    action = choose_action(event)
    notification = build_notification(event, action)
    shared_tags = {
        "currency": event.currency,
        "status": event.status.value,
        "action": action.value,
    }
    timestamp = event.occurred_at.astimezone(timezone.utc).isoformat()

    # Canonical call pattern: MetricsClient.report
    metrics.report(
        name="fintech.payment.events",
        value=1,
        metric_type="counter",
        tags=shared_tags,
        timestamp=timestamp,
        idempotency_key=f"{event.event_id}:payment-events:v1",
    )
    metrics.report(
        name="fintech.payment.risk_score",
        value=event.risk_score,
        metric_type="gauge",
        tags={"currency": event.currency, "action": action.value},
        timestamp=timestamp,
        idempotency_key=f"{event.event_id}:risk-score:v1",
    )
    return PaymentDecision(
        event_id=event.event_id,
        action=action,
        notification=notification,
        metrics_reported=2,
    )


app = FastAPI(title="Payment risk metrics")


@app.post("/payments/events", response_model=PaymentDecision)
def receive_payment(event: PaymentEvent) -> PaymentDecision:
    try:
        return process_payment(event, MetricsClient())
    except InfraiError as exc:
        client_status = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(
            status_code=client_status,
            detail={"code": exc.code, "message": str(exc.detail.get("message", "request rejected"))},
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Metric transport failed") from exc


if __name__ == "__main__":
    sample = PaymentEvent(
        event_id="evt_demo_001",
        account_id="acct_demo_001",
        amount_minor=14900,
        currency="USD",
        status=PaymentStatus.AUTHORIZED,
        risk_score=72,
        occurred_at=datetime.now(timezone.utc),
    )
    print(process_payment(sample, MetricsClient()).model_dump_json(indent=2))
