SHELL := /bin/bash
.PHONY: bootstrap ecs-fargate benchmark teardown report workload-docker

# Deploy the bootstrap stack through a change set. Needs AWS_PROFILE (SSO or `aws login`) and BUDGET_EMAIL.
# Set OIDC_PROVIDER_ARN when the account already has the GitHub OIDC provider.
bootstrap:
	uv run --locked python scripts/deploy_stack.py --stack agentic-platform-advisor-bootstrap \
		--template infra/cfn/00-bootstrap.yaml --env-param BudgetEmail=BUDGET_EMAIL \
		--optional-env-param ExistingOidcProviderArn=OIDC_PROVIDER_ARN

# Deploy the network stack, then the ecs-fargate cell. Needs AWS_PROFILE, RUN_ID and IMAGE_URI (image in ECR).
ecs-fargate:
	@test -n "$(RUN_ID)" || { echo "error: RUN_ID is not set. Run: make ecs-fargate RUN_ID=<id>"; exit 1; }
	@test -n "$$IMAGE_URI" || { echo "error: IMAGE_URI is not set. Export the ECR image URI."; exit 1; }
	uv run --locked python scripts/deploy_stack.py --stack $(RUN_ID)-network \
		--template infra/cfn/10-network.yaml --env-param RunId=RUN_ID
	NETWORK_STACK=$(RUN_ID)-network uv run --locked python scripts/deploy_stack.py --stack $(RUN_ID)-ecs-fargate \
		--template infra/cfn/30-ecs.yaml --env-param RunId=RUN_ID \
		--env-param NetworkStackName=NETWORK_STACK --env-param ImageUri=IMAGE_URI

benchmark teardown report:
	@echo "not implemented yet"; exit 1

# Build and run the workload image on :8080. LLM_MODE=bedrock uses BEDROCK_MODEL_ID, default amazon.nova-micro-v1:0.
workload-docker:
	docker build -t triage-workload workload/
	docker run --rm -p 8080:8080 -e LLM_MODE=$${LLM_MODE:-mock} -e BEDROCK_MODEL_ID triage-workload
