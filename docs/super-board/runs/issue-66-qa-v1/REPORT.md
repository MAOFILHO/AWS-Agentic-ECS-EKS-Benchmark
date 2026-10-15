# Issue #66 · QA v1 · head `ad84338`

Card: helm workload chart for the EKS cells (PR #71). Non-visual: no screenshots.

| AC | Result | Proof |
|---|---|---|
| `helm lint` exits 0 for each values file | ✅ | CI `helm` job, run 38792380382 on `ad84338`: `1 chart(s) linted, 0 chart(s) failed` twice · `ci-helm-job.log` |
| `helm template` renders Deployment, Service, Ingress, HPA for both values files | ✅ | `tests/test_helm_chart.py::test_renders_all_kinds[values-ec2/fargate]` passed in CI |
| Test asserts HPA 60, `/readyz`, 256m/512Mi, non-root, no privileged | ✅ | `test_hpa_targets_cpu_60`, `test_ingress_is_public_alb_on_readyz`, `test_container_size_probes_and_security` (13 passed in CI) |
| CI runs `helm lint` | ✅ | `.github/workflows/ci.yml` job `helm`, SUCCESS on `ad84338` |
| `ruff check` and `pytest` pass | ✅ | local `ruff.log` exit 0 · local `pytest.log` 118 passed, 13 skipped (no helm locally) · CI `lint` and `test` SUCCESS |
| No 12-digit account id or email | ✅ | `secrets-grep.log` grep exit 1 over chart, test and CI files |

Checks beyond the AC:
- Image user is uid 10001 (`workload/Dockerfile`), matching `runAsUser: 10001`.
- The workload writes no files, so `readOnlyRootFilesystem: true` is safe.

Test gaps (not written):
- Medium: `pyyaml` is used by the test but is only a transitive dependency (via langchain-core), not a direct dev dependency.
- Low: no test asserts the Service selector matches the pod labels or the Ingress backend port matches the Service port.
- Low: helm chart tests skip locally when `helm` is not installed; only CI runs them.
