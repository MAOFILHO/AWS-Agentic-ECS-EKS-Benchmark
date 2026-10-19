"""The two EKS cells get the same pod size and scaling target (issue #153). Reads the values files, no helm."""

from pathlib import Path

import yaml

CHART = Path(__file__).resolve().parent.parent / "infra" / "helm" / "workload"


def _merged(values_file):
    """values.yaml with one cell file on top, as Helm merges them (top-level keys)."""
    return {**yaml.safe_load((CHART / "values.yaml").read_text()), **yaml.safe_load((CHART / values_file).read_text())}


def test_fargate_and_ec2_get_the_same_cpu_and_hpa_and_fargate_names_its_billed_size():
    ec2, fargate = _merged("values-ec2.yaml"), _merged("values-fargate.yaml")
    assert {k: v for k, v in ec2.items() if k != "nodeSelector"} == {k: v for k, v in fargate.items() if k != "nodeSelector"}
    assert fargate["resources"]["requests"]["cpu"] == fargate["resources"]["limits"]["cpu"] == "256m"
    assert fargate["hpa"]["cpuTarget"] == 60
    # The one difference Fargate forces is written in the file: it bills the pod at its next size.
    assert "0.25 vCPU and 1 GB" in (CHART / "values-fargate.yaml").read_text()
