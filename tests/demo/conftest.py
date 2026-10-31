"""Guard all demo-directory tests and report only selected rehearsal scenes."""

import os
from pathlib import Path
import shutil

import pytest

from .harness import Harness, export_head, install_process_guard, isolated_imports, pin_demo
from .network import install_guard
from .support import DemoReport


ROOT = Path(__file__).resolve().parents[2]
# Capture before the root autouse fixture pins and clears mode variables.
EXPECTED_PATH = os.environ.get("DEMO_EXPECTED")
BACKEND = os.environ.get("LLM_BACKEND", "codex")
REPORT = DemoReport(ROOT / ".local" / "demo_test_report.md")


@pytest.fixture(autouse=True)
def network_guard(monkeypatch):
    install_guard(monkeypatch)


@pytest.fixture(scope="session")
def committed_tree(tmp_path_factory):
    template = tmp_path_factory.mktemp("demo-source")
    head = export_head(ROOT, template)
    return template, head


@pytest.fixture
def demo_environment(isolated_settings, tmp_path, monkeypatch, committed_tree):
    template, head = committed_tree
    copy = tmp_path / "repository"
    shutil.copytree(template, copy)
    with isolated_imports(copy):
        monkeypatch.chdir(copy)
        pin_demo(monkeypatch, copy, ROOT / "tests" / "conftest.py", BACKEND)
        install_process_guard(monkeypatch, copy)
        from core.config import config_line
        line = config_line()
        if not REPORT.config:
            REPORT.config = line
            REPORT.soft.append({"source_head": head[:8]})
        yield Harness(copy, monkeypatch, REPORT)


@pytest.fixture
def expected_path():
    return EXPECTED_PATH


@pytest.fixture
def kera(demo_environment):
    demo_environment.prepare_kera()
    return demo_environment


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if item.get_closest_marker("demo") is None:
        return
    label = "t06 (report only)" if item.name == "test_kera_t06_report" else item.name
    if report.when == "call" or (report.when == "setup" and not report.passed):
        status = "SKIP" if report.skipped else "FAIL" if report.failed else "PASS"
        detail = ""
        if call.excinfo:
            error = call.excinfo.value
            if isinstance(error, (AssertionError, pytest.skip.Exception, pytest.fail.Exception)):
                detail = str(error)
            else:
                detail = f"{type(error).__name__}: scene raised an error; see pytest output"
        REPORT.add(label, status, detail)


def pytest_sessionfinish(session, exitstatus):
    if REPORT.scenes:
        REPORT.write()


def pytest_terminal_summary(terminalreporter):
    if REPORT.scenes:
        terminalreporter.write_sep("=", "Offline demo scenes")
        terminalreporter.write_line(REPORT.render())
