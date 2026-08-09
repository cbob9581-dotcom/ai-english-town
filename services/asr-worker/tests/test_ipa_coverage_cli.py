import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ipa-coverage.py"


def test_cli_mock_verdict_ok():
    out = subprocess.run([sys.executable, str(SCRIPT), "--mock"], capture_output=True, text=True)
    assert out.returncode == 0
    import json
    report = json.loads(out.stdout)
    assert report["verdict"] == "ok"
    assert report["total"] == 4
