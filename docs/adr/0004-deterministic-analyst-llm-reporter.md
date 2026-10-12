# 0004. Deterministic analyst, LLM reporter

Status: proposed

## Context

The report must be readable and give a recommendation. An LLM writes good prose but can invent
or misquote numbers. A wrong number in a cost report breaks trust in the whole repo.

## Decision

- The `analyst` node is plain Python. It computes every figure (percentiles, error rate,
  scale-out time, cold start, $ per 1k requests, idle $ per hour) and writes `metrics.json`.
- Each figure in `metrics.json` names the raw file it came from.
- The `reporter` node is an LLM. It writes the narrative and a recommendation by team size and
  traffic band, from `metrics.json` only.
- A check extracts every number from the report and rejects it if the number is not in
  `metrics.json`. The check has its own test.

## Consequences

- Analyst math has unit tests with fixtures and needs no AWS.
- The reporter cannot compute new figures, such as a percentage difference. Any derived figure
  must be added to the analyst first.
- A rejected report fails the run step and keeps `metrics.json`, so it can be rerun cheaply.
