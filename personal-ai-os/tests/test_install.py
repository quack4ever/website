"""Installation tests.

These actually run install.sh and uninstall.sh against a throwaway HOME. An
installer that has never been executed is not an installer, it is a wish.

They are marked slow because they create a virtual environment.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")


def _run(script: str, home: Path, *args: str, timeout: int = 300):
    """Run an installer script with a completely clean environment."""
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
    return subprocess.run([BASH, str(ROOT / script), *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


def _assistant(home: Path, *args: str, timeout: int = 120):
    launcher = home / ".local" / "bin" / "assistant"
    env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "TERM": "dumb", "NO_COLOR": "1"}
    return subprocess.run([str(launcher), *args], env=env, capture_output=True,
                          text=True, timeout=timeout)


def _paios_home(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "PersonalAIOS"
    return home / ".local" / "share" / "personal-ai-os"


@pytest.fixture()
def fake_home(tmp_path):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "notes.txt").write_text("a document about titration")
    return home


# ===========================================================================
# Scripts are at least syntactically valid - cheap, always run
# ===========================================================================
@pytest.mark.parametrize("script", ["install.sh", "uninstall.sh", "bin/assistant"])
def test_shell_scripts_parse(script):
    result = subprocess.run([BASH, "-n", str(ROOT / script)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", ["install.sh", "uninstall.sh"])
def test_scripts_are_executable(script):
    assert os.access(ROOT / script, os.X_OK), "%s is not executable" % script


def test_help_works_without_installing_anything():
    result = _run("install.sh", Path("/nonexistent"), "--help", timeout=30)
    assert result.returncode == 0
    assert "Personal AI OS" in result.stdout


def test_installer_refuses_to_run_as_root_without_the_explicit_flag(tmp_path):
    """Running as root would give the assistant more power than the user."""
    if os.getuid() != 0:
        pytest.skip("only meaningful when the test runs as root")
    result = _run("install.sh", tmp_path, timeout=60)
    assert result.returncode != 0
    assert "Do not run this with sudo" in result.stderr


# ===========================================================================
# The real thing
# ===========================================================================
ROOT_ARGS = ["--allow-root"] if os.getuid() == 0 else []


@pytest.mark.slow
def test_full_install_then_use_then_uninstall(fake_home):
    install = _run("install.sh", fake_home, "--no-cloud", *ROOT_ARGS)
    assert install.returncode == 0, install.stdout + install.stderr
    assert "Installed." in install.stdout

    home = _paios_home(fake_home)
    assert (home / "db" / "assistant.db").exists()
    assert (home / "config.json").exists()
    assert (fake_home / ".local" / "bin" / "assistant").exists()

    # The installed launcher must actually work.
    assert "Personal AI OS" in _assistant(fake_home, "--version").stdout

    granted = _assistant(fake_home, "permissions", "grant",
                         str(fake_home / "Documents"))
    assert granted.returncode == 0

    assert _assistant(fake_home, "index", "build").returncode == 0
    found = _assistant(fake_home, "index", "search", "titration")
    assert "notes.txt" in found.stdout

    # --- uninstall keeping data -------------------------------------------
    removal = _run("uninstall.sh", fake_home)
    assert removal.returncode == 0
    assert not (fake_home / ".local" / "bin" / "assistant").exists()
    assert not (home / "app").exists()
    assert (home / "db" / "assistant.db").exists(), "data was deleted without --purge-data"
    assert (fake_home / "Documents" / "notes.txt").exists(), "a user file was deleted!"


@pytest.mark.slow
def test_reinstalling_keeps_your_settings(fake_home):
    _run("install.sh", fake_home, "--no-cloud", *ROOT_ARGS)
    _assistant(fake_home, "permissions", "grant", str(fake_home / "Documents"))
    _run("uninstall.sh", fake_home)
    _run("install.sh", fake_home, "--no-cloud", *ROOT_ARGS)

    permissions = _assistant(fake_home, "permissions")
    assert "Documents" in permissions.stdout, "the granted folder was forgotten"


@pytest.mark.slow
def test_purge_removes_data_but_never_your_documents(fake_home):
    _run("install.sh", fake_home, "--no-cloud", *ROOT_ARGS)
    home = _paios_home(fake_home)
    assert home.exists()

    result = _run("uninstall.sh", fake_home, "--purge-data", "--yes")
    assert result.returncode == 0
    assert not home.exists()
    assert (fake_home / "Documents" / "notes.txt").exists(), \
        "--purge-data deleted a user document!"


@pytest.mark.slow
def test_purge_without_confirmation_does_nothing(fake_home):
    _run("install.sh", fake_home, "--no-cloud", *ROOT_ARGS)
    home = _paios_home(fake_home)
    result = subprocess.run(
        [BASH, str(ROOT / "uninstall.sh"), "--purge-data"],
        env={"HOME": str(fake_home), "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "TERM": "dumb", "NO_COLOR": "1"},
        input="no thanks\n", capture_output=True, text=True, timeout=120)
    assert "Cancelled" in result.stdout
    assert home.exists(), "data was purged despite the confirmation failing"


@pytest.mark.slow
def test_installed_system_is_locked_down_by_default(fake_home):
    """A fresh install must be able to read nothing at all."""
    _run("install.sh", fake_home, "--no-cloud", *ROOT_ARGS)
    permissions = _assistant(fake_home, "permissions")
    assert "none" in permissions.stdout.lower()

    doctor = _assistant(fake_home, "doctor")
    assert "Folder access" in doctor.stdout
    assert "granted" in doctor.stdout
