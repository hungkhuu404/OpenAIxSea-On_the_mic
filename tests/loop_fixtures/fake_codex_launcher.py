"""Run the fake worker as a child, like a command launcher; no model calls."""

import subprocess
import sys


args = [sys.executable, *sys.argv[1:]]
is_job = "exec" in sys.argv[2:] and "--help" not in sys.argv[2:]
result = subprocess.run(args, input=sys.stdin.read() if is_job else None,
                        text=True, encoding="utf-8", timeout=15, check=False)
raise SystemExit(result.returncode)
