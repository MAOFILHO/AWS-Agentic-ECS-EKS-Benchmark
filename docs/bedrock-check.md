# Bedrock mode check

One real Bedrock request against the triage workload, run by the repo owner on 2026-10-12.
Source: owner evidence comment on issue #8.

| Field | Value |
|---|---|
| Model id | `amazon.nova-micro-v1:0` |
| Region | `us-east-1` |
| Mode | `LLM_MODE=bedrock` |
| HTTP status | 200 |
| Latency | 1.39 s (1.393676 s) |
| Category returned | `billing` |

The latency is one cold single request. It is not a benchmark figure.

## Command the owner ran

```bash
docker run --rm -p 8080:8080 \
  -e LLM_MODE=bedrock \
  -e BEDROCK_MODEL_ID=amazon.nova-micro-v1:0 \
  -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  triage-workload

curl -s -w "\n%{http_code} %{time_total}s\n" -X POST localhost:8080/triage \
  -H 'content-type: application/json' \
  -d '{"text":"I was charged twice for my order"}'
```

The three credential variables are short-lived values from an AWS sign-in profile, passed by name.
No account id or credential is recorded here.

## Finding

At the time of this check the service had no default for `BEDROCK_MODEL_ID` and raised `KeyError` when it was unset, so the check set it explicitly.
Issue #17 added the default `amazon.nova-micro-v1:0`.
