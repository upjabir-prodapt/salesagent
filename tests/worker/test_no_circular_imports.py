"""Regression test: the worker's real import graph must not have cycles.

Found during final verification of the agent pipeline rewrite: importing
`src.worker.dependencies` (as the real worker app does at startup) used to
raise `ImportError: cannot import name 'ReportCompiler' from partially
initialized module 'src.worker.agents.compiler' (most likely due to a
circular import)`.

Root cause: `services/__init__.py` eagerly imported `job_runner`, which
imports `worker.pipeline`, which imports `agents.compiler`, which imports
`services.formatting` -- and importing any submodule of `services` first
executes `services/__init__.py`, completing the cycle. The fix removes
the eager `job_runner` import from `services/__init__.py` (callers import
it directly: `from src.worker.services.job_runner import ResearchJobRunner`).

This test exercises the exact import path the running application takes
(`worker.main` / `worker.dependencies`) in a subprocess, since pytest's
own import order can mask circular imports that only manifest on a fresh
interpreter -- which is exactly what happened here.
"""

from __future__ import annotations

import subprocess
import sys


def test_worker_dependencies_import_without_circular_import_error():
    script = (
        "import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'tests');"
        "import tests._bootstrap;"
        "from src.worker.dependencies import build_research_pipeline, "
        "get_research_job_runner, get_research_task_handler;"
        "from src.worker.main import app;"
        "pipeline = build_research_pipeline();"
        "print('OK', type(pipeline).__name__)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"worker.dependencies import failed:\nSTDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    assert "OK ResearchPipeline" in result.stdout


def test_api_main_imports_without_circular_import_error():
    script = (
        "import sys; sys.path.insert(0, '.'); sys.path.insert(0, 'tests');"
        "import tests._bootstrap;"
        "from src.api.main import app;"
        "print('OK', app.title)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"api.main import failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
