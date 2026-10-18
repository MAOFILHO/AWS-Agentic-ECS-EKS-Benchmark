SHELL := /bin/bash
.PHONY: bootstrap ecs-fargate ecs-ec2 eks-ec2 eks-fargate benchmark teardown report chart aggregate workload-docker push-image push-k6 mirror-base

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

# Deploy the network stack, then the ecs-ec2 cell (30-ecs with ComputeType=EC2). Same needs as ecs-fargate.
ecs-ec2:
	@test -n "$(RUN_ID)" || { echo "error: RUN_ID is not set. Run: make ecs-ec2 RUN_ID=<id>"; exit 1; }
	@test -n "$$IMAGE_URI" || { echo "error: IMAGE_URI is not set. Export the ECR image URI."; exit 1; }
	uv run --locked python scripts/deploy_stack.py --stack $(RUN_ID)-network \
		--template infra/cfn/10-network.yaml --env-param RunId=RUN_ID
	NETWORK_STACK=$(RUN_ID)-network COMPUTE_TYPE=EC2 uv run --locked python scripts/deploy_stack.py \
		--stack $(RUN_ID)-ecs-ec2 --template infra/cfn/30-ecs.yaml --env-param RunId=RUN_ID \
		--env-param NetworkStackName=NETWORK_STACK --env-param ImageUri=IMAGE_URI \
		--env-param ComputeType=COMPUTE_TYPE

# Deploy the network stack, an EKS cell (40-eks), the controller role (41-eks-lbc-iam), then Helm installs the
# AWS Load Balancer Controller and the workload chart. Needs AWS_PROFILE, RUN_ID, IMAGE_URI (repo:tag in ECR),
# a region (AWS_REGION, AWS_DEFAULT_REGION or ARGS=--region ...), aws, helm and kubectl. Prints the ALB DNS name.
eks-ec2 eks-fargate:
	uv run --locked python -m scripts.deploy_eks --cell $@ --run-id "$(RUN_ID)" $(ARGS)

# Delete every stack of one run, newest first, then check nothing tagged run-id=RUN_ID is left.
# Needs AWS_PROFILE and a region (AWS_REGION or AWS_DEFAULT_REGION). Asks y/N first.
teardown:
	uv run --locked python scripts/delete_run.py --run-id "$(RUN_ID)"

# Plan one run, print its estimate and matrix, then ask y/N before any spend. Needs RUN_ID and a region
# (AWS_REGION, AWS_DEFAULT_REGION or ARGS=--region ...). A yes deploys every cell and always tears it down;
# that needs AWS_PROFILE, IMAGE_URI and K6_IMAGE_URI (repo:tag in ECR; make push-k6 prints the k6 one).
# ARGS="--cells ecs-fargate,eks-ec2 --profile spike,scale-from-min --auto-approve-under 3.00" narrows it.
# --cells and --profile each take a comma-separated list; leaving --profile out runs all 4.
# Resume a run: uv run --locked python -m advisor resume <run-id>.
benchmark:
	@test -n "$(RUN_ID)" || { echo "error: RUN_ID is not set. Run: make benchmark RUN_ID=<id>"; exit 1; }
	uv run --locked python -m advisor run --run-id "$(RUN_ID)" $(ARGS)

# Write results/RUN_ID/report.md from results/RUN_ID/metrics.json: the reporter only, no AWS deploy.
# Bedrock is still called: needs AWS_PROFILE and a region (AWS_REGION, AWS_DEFAULT_REGION or ARGS=--region ...).
report:
	@test -n "$(RUN_ID)" || { echo "error: RUN_ID is not set. Run: make report RUN_ID=<id>"; exit 1; }
	uv run --locked python -m scripts.make_report --run-id "$(RUN_ID)" $(ARGS)

# Draw results/RUN_ID/chart.svg from results/RUN_ID/metrics.json. ARGS="--out <path>" writes it elsewhere.
chart:
	@test -n "$(RUN_ID)" || { echo "error: RUN_ID is not set. Run: make chart RUN_ID=<id>"; exit 1; }
	uv run --locked python -m scripts.make_chart --run-id "$(RUN_ID)" $(ARGS)

# Write results/OUT/aggregate.json from results/<id>/metrics.json of each run in RUNS: n, min, median, max per
# figure. No AWS call. Then make chart RUN_ID=OUT draws the median with a min to max whisker.
aggregate:
	@test -n "$(RUNS)" || { echo "error: RUNS is not set. Run: make aggregate RUNS=a,b,c OUT=<name>"; exit 1; }
	@test -n "$(OUT)" || { echo "error: OUT is not set. Run: make aggregate RUNS=a,b,c OUT=<name>"; exit 1; }
	uv run --locked python -m scripts.aggregate_runs --runs "$(RUNS)" --out "results/$(OUT)/aggregate.json"

# Build and run the workload image on :8080. LLM_MODE=bedrock uses BEDROCK_MODEL_ID, default amazon.nova-micro-v1:0.
workload-docker:
	docker build -t triage-workload workload/
	docker run --rm -p 8080:8080 -e LLM_MODE=$${LLM_MODE:-mock} -e BEDROCK_MODEL_ID triage-workload

# Build workload/ and push it to ECR, tagged with the short git sha. Needs AWS_PROFILE and a region
# (AWS_REGION, AWS_DEFAULT_REGION or ARGS=--region ...). ARGS="--base-image <tag mirror-base printed>" builds
# from the mirror; ARGS=--allow-dirty pushes uncommitted changes. Images are linux/amd64 (Fargate x86_64)
# even on an M1 Mac; ARGS="--platform linux/arm64" overrides it for Graviton.
push-image:
	uv run --locked python scripts/push_image.py $(ARGS)

# Build loadtests/ FROM the mirrored k6 base (run mirror-base first) and push it to ECR as triage-workload:k6-<sha>,
# linux/amd64. Prints the image URI: export it as K6_IMAGE_URI for make benchmark. Same needs and ARGS as push-image.
push-k6:
	uv run --locked python scripts/push_image.py --k6 $(ARGS)

# Mirror python:3.12-slim and the pinned uv image into the base-images ECR repository.
mirror-base:
	uv run --locked python scripts/push_image.py --mirror-base $(ARGS)
