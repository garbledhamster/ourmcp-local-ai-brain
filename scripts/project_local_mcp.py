from __future__ import annotations

from pathlib import Path
import os
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
os.environ["PYTHONPATH"] = str(SCRIPTS_DIR) + os.pathsep + os.environ.get("PYTHONPATH", "")
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
os.environ["LOCAL_AI_BRAIN_HOME"] = str(PROJECT_ROOT / "brain")

from local_ai_brain.project_local_mcp import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
