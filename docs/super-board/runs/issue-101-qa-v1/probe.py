import sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests")
from test_json_boundaries import unmodelled
from advisor.schemas import read_metrics, MetricsError
d = Path(tempfile.mkdtemp())
(d/"alias.py").write_text("import json as j\ndef r(p):\n    return j.load(open(p))\n")
(d/"fromimp.py").write_text("from json import loads\ndef r(s):\n    return loads(s)\n")
(d/"modlevel.py").write_text("import json\nX = json.loads('{}')\n")
(d/"cls.py").write_text("import json\nclass A:\n    def r(self, p):\n        return json.load(open(p))\n")
print("scan:", unmodelled(d))
m = d/"metrics.json"
for body in ['{"run_id":"r1","figures":[]}', '{"run_id":"r1"}', '{}', '[]', '']:
    m.write_text(body)
    try: print(repr(body), "->", read_metrics(m))
    except MetricsError as e: print(repr(body), "-> MetricsError")
try: read_metrics(d/"missing.json")
except Exception as e: print("missing ->", type(e).__name__)
