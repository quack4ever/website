"""Shared test setup.

EVERY test runs against a throwaway PAIOS_HOME inside a temporary directory.
Nothing here can touch your real configuration, database, or files - which is
exactly the property we want, given that some of these tests deliberately try
to delete things.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    """A completely isolated installation plus a fake 'user home' to play in."""
    from assistant import config, db, paths

    paios_home = tmp_path / "paios"
    user_home = tmp_path / "userhome"
    (user_home / "Documents").mkdir(parents=True)
    (user_home / "Downloads").mkdir(parents=True)
    (user_home / ".ssh").mkdir(parents=True)
    (user_home / ".ssh" / "id_rsa").write_text("PRETEND PRIVATE KEY")
    (user_home / "Documents" / "notes.txt").write_text("hello world\nsecond line\n")

    monkeypatch.setenv("PAIOS_HOME", str(paios_home))
    monkeypatch.setenv("HOME", str(user_home))

    db.close_all()
    paths.ensure_dirs()
    conn = db.connect()
    config.init_default()

    class Sandbox:
        home = paios_home
        user = user_home
        docs = user_home / "Documents"
        downloads = user_home / "Downloads"
        connection = conn

        @staticmethod
        def cfg():
            return config.load()

    yield Sandbox()
    db.close_all()
