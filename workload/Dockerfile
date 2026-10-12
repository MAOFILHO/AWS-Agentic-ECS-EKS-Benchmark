# Build context is workload/ (docker build workload/), so dependencies come from
# requirements.txt, exported from the root uv.lock:
#   uv export --no-dev --no-hashes --frozen --no-emit-project -o workload/requirements.txt
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /srv
COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

RUN useradd --system --uid 10001 --no-create-home app
COPY --chown=app:app . workload/
USER 10001

# Default region; IaC overrides it. Bedrock mode also needs BEDROCK_MODEL_ID.
ENV AWS_DEFAULT_REGION=us-east-1

EXPOSE 8080
CMD ["uvicorn", "workload.app:app", "--host", "0.0.0.0", "--port", "8080"]
