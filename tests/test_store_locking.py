"""Regression coverage for concurrent writers on one index (review I14).

Rule (docs/spec-tech/persistence.md): opening a current index never takes the
SQLite write lock, and a writer that meets another writer's lock fails with a
clear `StoreLockedError`, never a raw `OperationalError`.
"""
import shutil
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from systemlens.delivery.cli import app
from systemlens.infrastructure.paths import db_path
from systemlens.storage import sqlite as store_module
from systemlens.storage.sqlite import Store, StoreLockedError

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _short_busy_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store_module, "BUSY_TIMEOUT_SECONDS", 0.1)


def _current_index(root: Path) -> None:
    with Store(root) as store:
        with store.transaction():
            store.set_meta("probe", "1")


def _hold_write_lock(root: Path) -> sqlite3.Connection:
    other = sqlite3.connect(db_path(root), timeout=0.1)
    other.execute("BEGIN IMMEDIATE")
    return other


def test_opening_a_current_index_takes_no_write_lock(tmp_path: Path) -> None:
    _current_index(tmp_path)
    other = _hold_write_lock(tmp_path)
    try:
        with Store(tmp_path) as store:
            assert store.get_meta("probe") == "1"
    finally:
        other.rollback()
        other.close()


def test_transaction_on_a_locked_index_raises_store_locked_error(tmp_path: Path) -> None:
    _current_index(tmp_path)
    other = _hold_write_lock(tmp_path)
    try:
        with Store(tmp_path) as store:
            with pytest.raises(StoreLockedError, match="Index verrouillé"):
                with store.transaction():
                    pass  # pragma: no cover - BEGIN IMMEDIATE fails first
    finally:
        other.rollback()
        other.close()


class _InterleavingConnection:
    """Let another writer take the lock right after a checkpoint commit."""

    def __init__(self, inner: sqlite3.Connection, root: Path) -> None:
        self._inner = inner
        self._root = root
        self.other: sqlite3.Connection | None = None

    def commit(self) -> None:
        self._inner.commit()
        if self.other is None:
            self.other = _hold_write_lock(self._root)

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_writer_interleaving_at_a_checkpoint_fails_cleanly(tmp_path: Path) -> None:
    _current_index(tmp_path)
    with Store(tmp_path) as store:
        proxy: _InterleavingConnection | None = None
        with pytest.raises(StoreLockedError, match="checkpoint"):
            with store.transaction():
                store.set_meta("code_flow_snapshot_status", "partial")
                proxy = _InterleavingConnection(store.conn, tmp_path)
                store._conn = proxy  # type: ignore[assignment]
                store.commit_checkpoint()
        assert proxy is not None and proxy.other is not None
        proxy.other.rollback()
        proxy.other.close()
        store._conn = proxy._inner
    with Store(tmp_path, readonly=True) as store:
        assert store.get_meta("code_flow_snapshot_status") == "partial"


def test_cli_index_on_a_locked_index_reports_a_clear_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURES / "endpoint_index_repo", repo)
    monkeypatch.chdir(repo)
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["index"]).exit_code == 0

    other = _hold_write_lock(repo)
    try:
        result = runner.invoke(app, ["index"])
    finally:
        other.rollback()
        other.close()

    assert result.exit_code == 2
    assert "Indexation impossible : Index verrouillé" in result.output
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)
