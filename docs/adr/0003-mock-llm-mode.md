# 0003. Mock LLM mode for platform-only measurement

Status: proposed

## Context

A real LLM call adds hundreds of milliseconds and varies with model load and quota. That noise
hides the platform overhead the benchmark is meant to measure, and it costs money on every request.

## Decision

- The workload reads `LLM_MODE`.
- `mock`: a fake LLM with a fixed latency, configurable, default 0 ms. Measures platform overhead only.
- `bedrock`: a real Bedrock call; the model id is a parameter. Measures end to end.
- The smoke test and default CI runs use `mock`.
- The run record stores the mode and the mock latency.

## Consequences

- Mock runs are cheap and repeatable, so they can run on every change.
- Mock results say nothing about model latency. The report labels the mode on every chart.
- `bedrock` runs need model access in the account and can hit throttling. Throttle counts go
  into the raw data.
