# The k6 image the harness task runs: the pinned k6 base plus the four profile scripts.
# Build context is loadtests/. The base is mirrored into the base-images repository by push_image.py --mirror-base.
ARG BASE_IMAGE=grafana/k6:1.3.0
FROM ${BASE_IMAGE}
COPY *.js /scripts/
