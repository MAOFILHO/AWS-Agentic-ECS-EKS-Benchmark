"""EKS cell deploy: input checks, stack and Helm order, temp kubeconfig, SSM version, masking (issue #68)."""

import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts import deploy_eks

ACCOUNT = "1234" * 3  # no 12-digit literal in a tracked file (test_bootstrap_oidc)
IMAGE = f"{ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/triage-workload:abc1234"
ALB = "k8s-default-workload-0a1b2c3d4e-987654321.us-east-1.elb.amazonaws.com"
OUTPUTS = {
    "r1-network": {"VpcId": "vpc-0abc"},
    "r1-eks-ec2": {"ClusterName": "r1-eks-ec2"},
    "r1-eks-fargate": {"ClusterName": "r1-eks-fargate"},
    "r1-eks-lbc-iam": {"LbcRoleArn": f"arn:aws:iam::{ACCOUNT}:role/r1-lbc"},
}


class FakeCfn:
    def describe_stacks(self, StackName):
        outs = [{"OutputKey": k, "OutputValue": v} for k, v in OUTPUTS[StackName].items()]
        return {"Stacks": [{"Outputs": outs}]}


class FakeSsm:
    def __init__(self, minors):
        self.minors = minors

    def get_parameters(self, Names):
        found = [n for n in Names if int(n.split("/")[5].split(".")[1]) in self.minors]
        return {"Parameters": [{"Name": n, "Value": "ami-0abc"} for n in found]}


class FakeEks:
    def __init__(self, events):
        self.events = events

    def get_waiter(self, name):
        events = self.events

        class Waiter:
            def wait(self, **kwargs):
                events.append(("wait", name, kwargs["clusterName"], kwargs["nodegroupName"]))

        return Waiter()


def runner(events, alb_after=1, fail=None, ready="1"):
    polls = []

    def run(args, check, capture_output, text):
        assert capture_output and text
        events.append(("run", *args))
        if fail and args[0] == fail:
            raise subprocess.CalledProcessError(1, args, stderr=f"denied for arn:aws:iam::{ACCOUNT}:role/x")
        out = ""
        if args[:3] == ["kubectl", "get", "ingress"]:
            polls.append(1)
            out = ALB if len(polls) >= alb_after else ""
        elif args[:3] == ["kubectl", "get", "deployment"]:
            out = ready
        elif args[0] == "kubectl":  # a diagnostics command
            out = f"{' '.join(args[1:3])}: AWS_ROLE_ARN=arn:aws:iam::{ACCOUNT}:role/r1-lbc account {ACCOUNT}\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    return run


def deploy(events, cell="eks-ec2", minors=range(30, 35), **kw):
    def deploy_one(cfn, stack, body, params):
        events.append(("stack", stack, params))

    return deploy_eks.deploy(
        FakeCfn(), FakeSsm(set(minors)), FakeEks(events), "r1", cell, "us-east-1", IMAGE,
        deploy_one=deploy_one, run=kw.pop("run", runner(events)), sleep=lambda s: None, **kw,
    )


def kind(event):
    if event[0] == "run":
        return " ".join(event[1:3]) if event[1] != "helm" else f"helm {event[4]}"
    return f"{event[0]} {event[1]}"


@pytest.mark.parametrize("unset", ["RUN_ID", "IMAGE_URI", "region"])
def test_missing_input_exits_before_any_aws_call(monkeypatch, unset):
    env = {"IMAGE_URI": IMAGE, "AWS_PROFILE": "sso", "AWS_REGION": "us-east-1"}
    argv = ["--cell", "eks-ec2", "--run-id", "r1"]
    if unset == "RUN_ID":
        argv = ["--cell", "eks-ec2"]
    elif unset == "IMAGE_URI":
        env.pop("IMAGE_URI")
    else:
        env.pop("AWS_REGION")
    for name in ("IMAGE_URI", "AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(deploy_eks.boto3, "Session", lambda **kw: pytest.fail("AWS called"))

    with pytest.raises(SystemExit) as exc:
        deploy_eks.main(argv)
    assert unset in str(exc.value)
    assert exc.value.code != 0


def test_stacks_then_kubeconfig_then_controller_then_workload():
    events = []
    host = deploy(events)

    assert [kind(e) for e in events] == [
        "stack r1-network",
        "stack r1-eks-ec2",
        "stack r1-eks-lbc-iam",
        "wait nodegroup_active",
        "aws eks",
        "helm aws-load-balancer-controller",
        "helm workload",
        "kubectl get",
    ]
    assert host == ALB
    lbc = events[5]
    assert lbc[lbc.index("--version") + 1] == deploy_eks.LBC_CHART_VERSION
    assert "vpcId=vpc-0abc" in lbc and "clusterName=r1-eks-ec2" in lbc
    workload = events[6]
    assert workload[workload.index("--values") + 1].endswith("values-ec2.yaml")
    assert f"image.repository={ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/triage-workload" in workload
    assert "image.tag=abc1234" in workload
    assert events[2][2] == {"RunId": "r1", "EksStackName": "r1-eks-ec2", "Cell": "eks-ec2"}


def test_fargate_cell_skips_node_group_and_uses_fargate_values():
    events = []
    deploy(events, cell="eks-fargate")

    assert not [e for e in events if e[0] == "wait"]
    assert events[1][1:] == ("r1-eks-fargate", {
        "RunId": "r1", "NetworkStackName": "r1-network", "ComputeType": "FARGATE",
        "KubernetesVersion": "1.34"})
    workload = [e for e in events if e[0] == "run" and e[1] == "helm"][1]
    assert workload[workload.index("--values") + 1].endswith("values-fargate.yaml")


def helm_installs(events):
    lbc, workload = [e for e in events if e[0] == "run" and e[1] == "helm"]
    return lbc, workload


def test_fargate_waits_longer_and_gives_the_controller_vpc_id_and_its_irsa_role():
    # Issue #153: Fargate pods have no node metadata and no Pod Identity, and each one waits for a micro VM.
    events = []
    deploy(events, cell="eks-fargate")
    lbc, workload = helm_installs(events)
    for install in (lbc, workload):
        assert install[install.index("--timeout") + 1] == deploy_eks.FARGATE_HELM_TIMEOUT == "15m"
    assert "vpcId=vpc-0abc" in lbc and "region=us-east-1" in lbc
    assert f"serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn=arn:aws:iam::{ACCOUNT}:role/r1-lbc" in lbc


def test_ec2_keeps_helm_default_wait_and_pod_identity():
    events = []
    deploy(events)
    lbc, workload = helm_installs(events)
    assert "--timeout" not in lbc + workload
    assert not any("role-arn" in arg for arg in lbc)
    assert "vpcId=vpc-0abc" in lbc


def test_kubernetes_version_comes_from_ssm_and_is_printed(capsys):
    events = []
    deploy(events, minors=range(30, 42))

    assert events[1][2]["KubernetesVersion"] == "1.41"
    assert "Kubernetes version 1.41" in capsys.readouterr().out


def test_no_ssm_parameter_stops_before_any_stack():
    events = []
    with pytest.raises(SystemExit, match="no EKS AMI public parameter"):
        deploy(events, minors=[])
    assert events == []


def test_kubeconfig_is_a_temp_file_and_home_is_untouched(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    events = []
    deploy(events)

    runs = [e for e in events if e[0] == "run"]
    paths = {e[e.index("--kubeconfig") + 1] for e in runs}
    assert len(paths) == 1
    kubeconfig = paths.pop()
    assert Path(kubeconfig).is_relative_to(deploy_eks.tempfile.gettempdir())
    assert not Path(kubeconfig).parent.exists()  # the temp dir is removed after the deploy
    assert os.path.expanduser("~/.kube/config") != kubeconfig
    assert not (tmp_path / ".kube").exists()


def test_waits_for_the_alb_hostname():
    events = []
    host = deploy(events, run=runner(events, alb_after=3))

    assert host == ALB
    assert len([e for e in events if e[1:3] == ("kubectl", "get")]) == 3


def test_alb_timeout_exits_non_zero():
    events = []
    with pytest.raises(SystemExit, match="no ALB hostname"):
        deploy(events, run=runner(events, alb_after=10**6), alb_timeout=0)


def test_output_never_contains_an_account_id(capsys):
    events = []
    deploy(events)
    out = capsys.readouterr()
    assert ALB in out.out
    assert not re.search(r"\b\d{12}\b", out.out + out.err)


def test_failed_helm_error_masks_the_account_id():
    events = []
    with pytest.raises(SystemExit) as exc:
        deploy(events, run=runner(events, fail="helm"))
    assert ACCOUNT not in str(exc.value)
    assert "helm upgrade failed" in str(exc.value)


def test_image_without_tag_is_refused():
    with pytest.raises(SystemExit, match="no tag"):
        deploy_eks.split_image("localhost:5000/triage-workload")


def test_aws_error_from_main_masks_the_account_id(monkeypatch):
    from botocore.exceptions import ClientError

    class DeniedSsm:
        def get_parameters(self, Names):
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": (
                f"User: arn:aws:sts::{ACCOUNT}:assumed-role/x/y is not authorized to perform: ssm:GetParameters"
            )}}, "GetParameters")

    class Session:
        def __init__(self, **kw):
            pass

        def client(self, name):
            return DeniedSsm() if name == "ssm" else object()

    for name, value in {"IMAGE_URI": IMAGE, "AWS_PROFILE": "sso", "AWS_REGION": "us-east-1"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(deploy_eks.boto3, "Session", Session)

    with pytest.raises(SystemExit) as exc:
        deploy_eks.main(["--cell", "eks-ec2", "--run-id", "r1"])
    assert exc.value.code != 0
    assert ACCOUNT not in str(exc.value)



def test_yes_answers_every_change_set_prompt(monkeypatch):
    seen = {}
    for name, value in {"IMAGE_URI": IMAGE, "AWS_PROFILE": "sso", "AWS_REGION": "us-east-1"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(deploy_eks.boto3, "Session", lambda **kw: type("S", (), {"client": lambda self, n: None})())
    monkeypatch.setattr(deploy_eks, "deploy", lambda *a, deploy_one, **kw: seen.update(one=deploy_one))
    deploy_eks.main(["--cell", "eks-ec2", "--run-id", "r1"])
    assert seen["one"] is deploy_eks.deploy_stack.deploy
    deploy_eks.main(["--cell", "eks-ec2", "--run-id", "r1", "--yes"])
    assert seen["one"].keywords["answer"]("Execute? ") == "y"


def test_ec2_cell_passes_the_instance_type_to_the_eks_stack():
    events = []
    deploy(events, instance_type="t3.medium")
    assert events[1][2]["InstanceType"] == "t3.medium"


def test_fargate_cell_ignores_the_instance_type():
    events = []
    deploy(events, cell="eks-fargate", instance_type="t3.medium")
    assert "InstanceType" not in events[1][2]


def test_main_reads_the_instance_type_from_the_env(monkeypatch):
    got = {}
    for name, value in {"IMAGE_URI": IMAGE, "AWS_PROFILE": "sso", "AWS_REGION": "us-east-1", "INSTANCE_TYPE": "t3.medium"}.items():
        monkeypatch.setenv(name, value)

    class Session:
        def __init__(self, **kw):
            pass

        def client(self, name):
            return name

    monkeypatch.setattr(deploy_eks.boto3, "Session", Session)
    monkeypatch.setattr(deploy_eks, "deploy", lambda *a, **kw: got.update(kw))
    deploy_eks.main(["--cell", "eks-ec2", "--run-id", "r1"])
    assert got["instance_type"] == "t3.medium"


class Clock:
    """Fake monotonic clock; each sleep moves it forward."""

    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_alb_timeout_writes_the_diagnostics_file_before_exiting(tmp_path):
    # Issue #155: deploy_eks exits only after the file is written, and the deployer runs teardown after that exit.
    events, path = [], tmp_path / "results" / "r1" / "raw" / "eks-diagnostics.txt"
    with pytest.raises(SystemExit) as exc:
        deploy(events, cell="eks-fargate", run=runner(events, alb_after=10**6), alb_timeout=0, diagnostics=path)
    assert "no ALB hostname" in str(exc.value) and str(path) in str(exc.value)
    text = path.read_text()
    for section in ("kubectl get pods -A -o wide", "kubectl describe ingress workload",
                    "kubectl describe pod -n kube-system", "kubectl logs -n kube-system", "--tail 200",
                    "kubectl get events -A --sort-by .lastTimestamp"):
        assert section in text, section
    diag = [e for e in events if e[0] == "run" and e[1] == "kubectl" and e[2] in ("describe", "logs")]
    assert diag and all("--kubeconfig" in e for e in diag)


def test_diagnostics_mask_account_ids_and_role_arns(tmp_path):
    events, path = [], tmp_path / "eks-diagnostics.txt"
    with pytest.raises(SystemExit):
        deploy(events, cell="eks-fargate", run=runner(events, alb_after=10**6), alb_timeout=0, diagnostics=path)
    text = path.read_text()
    assert ACCOUNT not in text and not re.search(r"\b\d{12}\b", text)
    assert "arn:aws:iam" not in text and "role/r1-lbc" not in text
    assert "<role-arn>" in text


def test_a_failed_controller_install_also_writes_diagnostics(tmp_path):
    events, path = [], tmp_path / "eks-diagnostics.txt"
    with pytest.raises(SystemExit) as exc:
        deploy(events, cell="eks-fargate", run=runner(events, fail="helm"), diagnostics=path)
    assert "helm upgrade failed" in str(exc.value) and path.is_file()


def test_unwritable_diagnostics_keep_the_original_error(tmp_path):
    # Teardown must still run: the deploy error comes back whatever the diagnostics do.
    events = []
    with pytest.raises(SystemExit) as exc:
        deploy(events, run=runner(events, alb_after=10**6), alb_timeout=0, diagnostics=tmp_path)  # a directory
    assert "no ALB hostname" in str(exc.value) and "could not be written" in str(exc.value)


def test_no_diagnostics_path_runs_no_diagnostics():
    events = []
    with pytest.raises(SystemExit):
        deploy(events, run=runner(events, alb_after=10**6), alb_timeout=0)
    assert not [e for e in events if e[0] == "run" and e[2] in ("describe", "logs")]


def test_a_not_ready_controller_stops_the_wait_after_5_minutes():
    events, clock = [], Clock()
    with pytest.raises(SystemExit) as exc:
        deploy_eks.wait_for_alb(runner(events, alb_after=10**6, ready=""), "kc", 900, sleep=clock.sleep, clock=clock)
    assert "aws-load-balancer-controller" in str(exc.value) and "not Ready after 300 s" in str(exc.value)
    assert 300 <= clock.now < 900


def test_a_ready_controller_keeps_waiting_until_the_alb_timeout():
    events, clock = [], Clock()
    with pytest.raises(SystemExit, match="no ALB hostname after 900 s"):
        deploy_eks.wait_for_alb(runner(events, alb_after=10**6, ready="1"), "kc", 900, sleep=clock.sleep, clock=clock)
    assert clock.now >= 900
    assert len([e for e in events if e[1:4] == ("kubectl", "get", "deployment")]) == 1


def test_irsa_trust_sub_is_the_service_account_the_controller_is_installed_with():
    template = (deploy_eks.CFN / "41-eks-lbc-iam.yaml").read_text()
    (sub,) = re.findall(r'"\$\{Issuer\}:sub": "([^"]+)"', template)
    assert sub == f"system:serviceaccount:{deploy_eks.LBC_NAMESPACE}:{deploy_eks.LBC_RELEASE}"
    events = []
    deploy(events, cell="eks-fargate")
    lbc, _ = helm_installs(events)
    assert lbc[lbc.index("--namespace") + 1] == deploy_eks.LBC_NAMESPACE
    assert f"serviceAccount.name={deploy_eks.LBC_RELEASE}" in lbc and "serviceAccount.create=true" in lbc


def test_main_passes_the_diagnostics_path(monkeypatch):
    got = {}
    for name, value in {"IMAGE_URI": IMAGE, "AWS_PROFILE": "sso", "AWS_REGION": "us-east-1"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(deploy_eks.boto3, "Session", lambda **kw: type("S", (), {"client": lambda self, n: n})())
    monkeypatch.setattr(deploy_eks, "deploy", lambda *a, **kw: got.update(kw))
    deploy_eks.main(["--cell", "eks-fargate", "--run-id", "r1", "--diagnostics", "results/x/raw/eks-diagnostics.txt"])
    assert got["diagnostics"] == "results/x/raw/eks-diagnostics.txt"
