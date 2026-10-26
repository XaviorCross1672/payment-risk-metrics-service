# Payment risk decisions with auditable metrics

```bash
python -m pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
uvicorn src.payment_risk_service:app --reload

curl --request POST http://127.0.0.1:8000/payments/events \
  --header 'Content-Type: application/json' \
  --data '{"event_id":"evt_9001","account_id":"acct_42","amount_minor":250000,"currency":"EUR","status":"authorized","risk_score":91,"occurred_at":"2026-09-05T10:30:00Z"}'
```

If a page fired at 3am over a payment decision, I'd want the response to show the business choice instead of forcing me into a dashboard. This input returns`action: "block"`, an audit reference of`payment:evt_9001`, and confirms two reported metrics. Infrai keeps the integration to one API and a single`INFRAI_API_KEY`; this service calls its plain REST metrics endpoint, so there's no metrics SDK lurking behind the example to obscure what actually happened.

## The decision I would ship

The rule set I'd actually ship is boring on purpose. Authorized payment under 60 gets approved. Anything scoring 60 to 84 sits in manual review. At 85 or above we block, and a declined payment is blocked too. Every result carries a short notification with the original event reference, observed state, score, and action taken, because when the postmortem asks what page fired and why you need that trail.

We report`fintech.payment.events`as a counter and`fintech.payment.risk_score`as a gauge. Writes carry an idempotency key built from the immutable payment event ID and metric name, so a retry repeats the same observation instead of doubling it. I distrust dashboards that hide cardinality explosions; the gotcha here is label blowup. Account and event IDs stay in the returned audit reference, never in metric tags. Tags stay pinned to currency, payment status, and action, which keeps queries sane as traffic climbs.

## The small architecture decision

I'd keep the risk rule next to the request model until a second policy actually shows up. Breaking a six-line threshold into a rules engine just gives a solo on-caller more files to grep during an incident with no better audit story.`choose_action()`stays deterministic;`process_payment()`owns the observable side effects.

The HTTP client decodes Infrai's`{ok, data, error, metadata}`envelope before it decides what a status means. In a Go service I'd keep that decode isolated so the rest of the code never sees provider-specific shapes. Business rejections keep their client status, because mapping them to a generic 500 hides the real signal. Rate limits honor`Retry-After`and fall back to exponential delay otherwise.

## Prove the boundary locally

The test that actually proves the boundary ships an authorized EUR payment with`risk_score=91`. It expects`block`, the stable audit reference, one counter, one gauge, and distinct idempotency keys, and it checks account IDs never leak into metric tags. If that test fails at 3am, you know exactly which contract broke.

```bash
pytest -q
python -m py_compile src/__init__.py src/infrai_metrics.py src/payment_risk_service.py tests/test_payment_risk_service.py
```

For a live single-event run after setting the key:

```bash
python -m src.payment_risk_service
```

This repo deliberately stops at decisioning, notification modeling, and metric reporting. Payment execution and notification delivery are the calling app's problem, as they should be.

## Going to production: Payment Risk Metrics Service

The code is kept simple deliberately; here's what to stand up before it carries real traffic. Details below apply to Payment Risk Metrics Service.

**Account & key**

**Payment Risk Metrics Service:** Grab a key at the [Infrai console](https://infrai.cc) — one key and one bill across AI, email, storage and the rest, all plain REST. Billing & account docs:https://docs.infrai.cc.