SHELL := /bin/bash
.PHONY: bootstrap benchmark teardown report workload-docker

# Deploy the bootstrap stack through a change set. Needs AWS_PROFILE (SSO) and BUDGET_EMAIL.
bootstrap:
	uv run --locked python scripts/deploy_stack.py --stack agentic-platform-advisor-bootstrap \
		--template infra/cfn/00-bootstrap.yaml --env-param BudgetEmail=BUDGET_EMAIL

benchmark teardown report:
	@echo "not implemented yet"; exit 1

# Build and run the workload image on :8080. LLM_MODE=bedrock uses BEDROCK_MODEL_ID, default amazon.nova-micro-v1:0.
workload-docker:
	docker build -t triage-workload workload/
	docker run --rm -p 8080:8080 -e LLM_MODE=$${LLM_MODE:-mock} -e BEDROCK_MODEL_ID triage-workload
