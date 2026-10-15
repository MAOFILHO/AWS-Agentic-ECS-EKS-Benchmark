"""Render infra/helm/workload with `helm template` and check it. No cluster needed."""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

CHART = Path(__file__).resolve().parent.parent / "infra" / "helm" / "workload"
VALUES_FILES = ["values-ec2.yaml", "values-fargate.yaml"]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")


def _render(values_file):
    out = subprocess.run(
        ["helm", "template", "workload", str(CHART), "-f", str(CHART / values_file)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {doc["kind"]: doc for doc in yaml.safe_load_all(out) if doc}


@pytest.fixture(params=VALUES_FILES)
def objs(request):
    return _render(request.param)


@pytest.mark.parametrize("values_file", VALUES_FILES)
def test_helm_lint(values_file):
    subprocess.run(["helm", "lint", str(CHART), "-f", str(CHART / values_file)], check=True)


def test_renders_all_kinds(objs):
    assert {"Deployment", "Service", "Ingress", "HorizontalPodAutoscaler"} <= objs.keys()


def test_hpa_targets_cpu_60(objs):
    spec = objs["HorizontalPodAutoscaler"]["spec"]
    assert spec["metrics"][0]["resource"]["target"]["averageUtilization"] == 60
    assert (spec["minReplicas"], spec["maxReplicas"]) == (1, 4)


def test_ingress_is_public_alb_on_readyz(objs):
    ing = objs["Ingress"]
    ann = ing["metadata"]["annotations"]
    assert ing["spec"]["ingressClassName"] == "alb"
    assert ann["alb.ingress.kubernetes.io/scheme"] == "internet-facing"
    assert ann["alb.ingress.kubernetes.io/target-type"] == "ip"
    assert ann["alb.ingress.kubernetes.io/healthcheck-path"] == "/readyz"
    assert ann["alb.ingress.kubernetes.io/listen-ports"] == '[{"HTTP": 80}]'


def test_service_is_clusterip_8080(objs):
    spec = objs["Service"]["spec"]
    assert spec["type"] == "ClusterIP"
    assert spec["ports"][0]["port"] == 8080


def test_container_size_probes_and_security(objs):
    pod = objs["Deployment"]["spec"]["template"]["spec"]
    (c,) = pod["containers"]
    size = {"cpu": "256m", "memory": "512Mi"}
    assert c["resources"] == {"requests": size, "limits": size}
    assert c["ports"][0]["containerPort"] == 8080
    assert c["livenessProbe"]["httpGet"]["path"] == "/healthz"
    assert c["readinessProbe"]["httpGet"]["path"] == "/readyz"
    assert {"name": "LLM_MODE", "value": "mock"} in c["env"]
    sc = c["securityContext"]
    assert sc["runAsNonRoot"] is True
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["readOnlyRootFilesystem"] is True
    assert sc.get("privileged", False) is False
    assert not pod.get("hostNetwork", False)


def test_node_selector_only_on_ec2():
    ec2 = _render("values-ec2.yaml")["Deployment"]["spec"]["template"]["spec"]
    fargate = _render("values-fargate.yaml")["Deployment"]["spec"]["template"]["spec"]
    assert ec2["nodeSelector"]
    assert "nodeSelector" not in fargate
