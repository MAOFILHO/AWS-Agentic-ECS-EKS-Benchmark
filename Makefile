SHELL := /bin/bash
.PHONY: bootstrap benchmark teardown report workload-docker

bootstrap benchmark teardown report:
	@echo "not implemented yet"; exit 1

# Build and run the workload image on :8080. LLM_MODE=bedrock also needs BEDROCK_MODEL_ID.
workload-docker:
	docker build -t triage-workload workload/
	docker run --rm -p 8080:8080 -e LLM_MODE=$${LLM_MODE:-mock} -e BEDROCK_MODEL_ID triage-workload
