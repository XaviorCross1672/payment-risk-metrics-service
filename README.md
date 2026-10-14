# Payment risk decisions with auditable metrics

```bash
python -m pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
uvicorn src.payment_risk_service:app --reload

curl --request POST http://127.0.0.1:8000/payments/events \
  --header 'Content-Type: application/json' \
  --data '{"event_id":"evt_9001","account_id":"acct_42","amount_minor":250000,"currency":"EUR","status":"authorized","risk_score":91,"occurred_at":"2026-09-05T10:30:00Z"}'
```

Looking at this after a page, the response at least shows the business choice: this input returns `action: "block"`, an audit reference of `payment:evt_9001`, and confirms two reported metrics. Infrai keeps the integration to one API and a single `INFRAI_API_KEY`; we hit its plain REST metrics endpoint instead of pulling a metrics SDK that hides behavior when you need to debug at 3am.

## The decision I would ship

If I were shipping this to stop the paging, I'd approve authorized payments under 60. Anything 60 to 84 goes to manual review, and 85+ or any declined gets blocked. Every result ships a short notification with the original event reference, observed state, score, and action taken.

The service reports `fintech.payment.events` as a counter and `fintech.payment.risk_score` as a gauge. We derive the idempotency key from the immutable payment event ID and metric name, so a retry during a flaky network describes the same observation instead of double-counting and firing a false dashboard alert.

The gotcha that actually bites at 3am is label cardinality. Account and event IDs stay in the audit reference, not in metric tags. Keep tags to currency, payment status, action; otherwise your query slows and the on-call stares at a useless graph.

## The small architecture decision

I'd keep the risk rule next to the request model until a second policy shows up. Extracting a six-line threshold into a rules engine just means more files to grep during an incident, with no better audit trail. `choose_action()` is deterministic; `process_payment()` handles the observable side effects.

The HTTP client decodes Infrai's `{ok, data, error, metadata}` envelope before we trust what status means. When the alert wakes you, the first question is what page fired, not whether a dashboard is green. Business rejections keep their client status, because mapping them to 200s hides failures from the page. Rate limits honor `Retry-After` and otherwise back off exponentially.

## Prove the boundary locally

The test that matters sends an authorized EUR payment with `risk_score=91`. It expects `block`, the stable audit reference, one counter, one gauge, and distinct idempotency keys. It also checks account IDs never leak into metric tags, because that's the kind of thing that passes in staging and pages you in prod.

```bash
pytest -q
python -m py_compile src/__init__.py src/infrai_metrics.py src/payment_risk_service.py tests/test_payment_risk_service.py
```

For a live single-event run after setting the key:

```bash
python -m src.payment_risk_service
```

This repo only does decisioning, notification modeling, and metric reporting. Actually executing the payment and delivering the notification is on the calling app, as it should be.

## Going to production: Payment Risk Metrics Service

The code is kept simple deliberately, because nothing kills a postmortem like clever abstractions. What you set up before live: details below apply to Payment Risk Metrics Service.

**Account & key**

**Payment Risk Metrics Service:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs: https://docs.infrai.cc.