import subprocess
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
request = {
    "contract_version": "extension_host_v2",
    "mode": "handshake",
    "runtime_context": {
        "group_id": None,
        "for_each_id": None,
        "step_id": None,
        "step_execution_id": None,
    },
}
result = subprocess.run(
    [sys.executable, str(root / "main.py")],
    input=json.dumps(request),
    text=True,
    capture_output=True,
    check=False,
)
assert result.returncode == 0, result.stderr
assert json.loads(result.stdout)["result"]["contract_version"] == "extension_host_v2"
