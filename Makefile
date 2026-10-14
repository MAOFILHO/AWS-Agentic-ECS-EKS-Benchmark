SHELL := /bin/bash
.PHONY: bootstrap benchmark teardown report workload-docker

# Deploy the bootstrap stack through a change set. Needs AWS_PROFILE (SSO or `aws login`) and BUDGET_EMAIL.
# Set OIDC_PROVIDER_ARN when the account already has the GitHub OIDC provider.
bootstrap:
	uv run --locked python scripts/deploy_stack.py --stack agentic-platform-advisor-bootstrap \
		--template infra/cfn/00-bootstrap.yaml --env-param BudgetEmail=BUDGET_EMAIL \
		--optional-env-param ExistingOidcProviderArn=OIDC_PROVIDER_ARN

# Delete every stack of one run, newest first, then check nothing tagged run-id=RUN_ID is left.
# Needs AWS_PROFILE and a region (AWS_REGION or AWS_DEFAULT_REGION). Asks y/N first.
teardown:
	uv run --locked python scripts/delete_run.py --run-id "$(RUN_ID)"

benchmark report:
	@echo "not implemented yet"; exit 1

# Build and run the workload image on :8080. LLM_MODE=bedrock uses BEDROCK_MODEL_ID, default amazon.nova-micro-v1:0.
workload-docker:
	docker build -t triage-workload workload/
	docker run --rm -p 8080:8080 -e LLM_MODE=$${LLM_MODE:-mock} -e BEDROCK_MODEL_ID triage-workload
