"""Every JSON read and write in advisor/ goes through a Pydantic model, or is on ALLOW with a reason."""

import ast
import math
from pathlib import Path

import pytest

from advisor import rawformats as rf
from advisor.schemas import MetricsError, RunConfig, read_metrics

ADVISOR = Path(__file__).resolve().parent.parent / "advisor"
JSON_CALLS = {"load", "loads", "dump", "dumps"}
# A function that names one of these validates its JSON with a Pydantic model.
MODEL_NAMES = {"TypeAdapter", "model_validate", "model_validate_json", "validate_python"}

# (file, function) -> why its JSON call needs no model.
ALLOW = {
    ("deployer.py", "run_logged"): "commands.jsonl audit line built here from argv, env names, exit and seconds; advisor never reads it",
    ("collector.py", "kubectl"): "kubectl -o json is an external API reply, like boto3; each event it yields goes through rf.write(list[Event])",
    ("load_runner.py", "extract_summary"): "k6 summary text; run_profile validates it with K6Summary before rf.write",
    ("pricing.py", "_products"): "AWS Pricing API PriceList strings, an external reply; read_prices demands exactly one USD price per key",
}


def _own_nodes(scope):
    """Nodes of `scope`, not of a function nested in it: a nested function is its own scope."""
    for child in ast.iter_child_nodes(scope):
        yield child
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield from _own_nodes(child)


def unmodelled(root: Path) -> list[tuple[str, str]]:
    """(file, function) of each json.load/loads/dump/dumps call whose function names no Pydantic model."""
    found = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text())
        # `import json as j` and `from json import loads as parse` count as json calls too.
        modules = {"json"} | {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                              for a in n.names if a.name == "json"}
        funcs = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "json"
                 for a in n.names if a.name in JSON_CALLS}
        for scope in [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))]:
            own = list(_own_nodes(scope))
            calls = [n for n in own if isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Attribute) and n.func.attr in JSON_CALLS
                 and isinstance(n.func.value, ast.Name) and n.func.value.id in modules)
                or (isinstance(n.func, ast.Name) and n.func.id in funcs))]
            names = {n.id for n in own if isinstance(n, ast.Name)} | {n.attr for n in own if isinstance(n, ast.Attribute)}
            if calls and not names & MODEL_NAMES:
                found.append((path.name, getattr(scope, "name", "<module>")))
    return found


def test_every_json_call_in_advisor_has_a_model_or_a_reason():
    assert set(unmodelled(ADVISOR)) == set(ALLOW)


def test_allow_list_entries_have_a_reason():
    assert all(len(why) > 20 for why in ALLOW.values())


def test_scan_catches_a_new_unmodelled_json_load(tmp_path):
    (tmp_path / "new.py").write_text("import json\n\ndef read(p):\n    return json.load(open(p))\n")
    (tmp_path / "ok.py").write_text("import json\nfrom pydantic import TypeAdapter\n\n"
                                    "def read(p):\n    return TypeAdapter(int).validate_python(json.load(open(p)))\n")
    assert unmodelled(tmp_path) == [("new.py", "read")]


def test_scan_catches_an_aliased_or_imported_json_call(tmp_path):
    (tmp_path / "alias.py").write_text("import json as j\n\ndef read(p):\n    return j.load(open(p))\n")
    (tmp_path / "named.py").write_text("from json import loads as parse\n\ndef read(s):\n    return parse(s)\n")
    assert unmodelled(tmp_path) == [("alias.py", "read"), ("named.py", "read")]


METRICS = '{"run_id": "r1", "figures": [{"name": "p50_ms", "value": %s, "unit": "ms", "source": "a/k6.json", "cell": "ecs-ec2"}]}'


def test_good_metrics_json_reads(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text(METRICS % "12.5")
    assert read_metrics(path).figures[0].value == 12.5


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", '"fast"'])
def test_nan_or_bad_metrics_json_raises_metrics_error(tmp_path, value):
    path = tmp_path / "metrics.json"
    path.write_text(METRICS % value)
    with pytest.raises(MetricsError, match="metrics.json"):
        read_metrics(path)


def test_unparseable_metrics_json_raises_metrics_error(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text("{not json")
    with pytest.raises(MetricsError):
        read_metrics(path)


def test_raw_write_rejects_data_that_fails_its_model(tmp_path):
    with pytest.raises(rf.RawFormatError):
        rf.write(tmp_path / "cost.json", {"load_usd": math.nan, "idle_usd_per_hour": 1.0}, rf.Cost)
    assert not (tmp_path / "cost.json").exists()


def test_run_config_rejects_unknown_keys_and_nan():
    good = (Path(__file__).resolve().parent.parent / "runs" / "default.json").read_text()
    assert RunConfig.model_validate_json(good).load.peak_rps > 0
    with pytest.raises(ValueError):
        RunConfig.model_validate_json(good.replace('"peak_rps": 50', '"peak_rps": 50, "extra": 1'))
    with pytest.raises(ValueError):
        RunConfig.model_validate_json(good.replace('"duration_s": 600', '"duration_s": NaN'))


def test_reporter_stops_on_a_nan_metrics_json_before_any_llm_call(tmp_path):
    from advisor.reporter import make_reporter
    from advisor.state import Capacity, CellSpec, RunState
    from advisor.store import create_run_dirs

    create_run_dirs("r1", tmp_path).metrics.write_text(METRICS % "NaN")
    state = RunState(run_id="r1", cells=[CellSpec(name="ecs-ec2", profile="steady")], region="us-east-1",
                     capacity=Capacity(vcpu=0.25, memory_mib=512, min_replicas=1, max_replicas=4))

    class NoLLM:
        def complete(self, prompt):
            raise AssertionError("the LLM must not see a bad metrics.json")

    with pytest.raises(MetricsError):
        make_reporter(NoLLM(), tmp_path)(state)
