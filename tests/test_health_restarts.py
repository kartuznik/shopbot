from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from bot import health
from bot.health import BotHealthMonitor


def _monitor(tmp_path: Path) -> BotHealthMonitor:
    return BotHealthMonitor(
        bot=SimpleNamespace(),
        db_path=str(tmp_path / 'shopbot.db'),
        data_dir=str(tmp_path),
        admin_ids=[],
        log_path=str(tmp_path / 'health.log'),
    )


class _FakeProcess:
    def __init__(self, stdout: bytes, returncode: int = 0) -> None:
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b''


def _patch_exec(monkeypatch: pytest.MonkeyPatch, result: object) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    async def fake_exec(*args: str, **_kwargs: object) -> object:
        calls.append(args)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(health.asyncio, 'create_subprocess_exec', fake_exec)
    return calls


def test_reads_nrestarts_from_systemd_not_journal(tmp_path, monkeypatch):
    calls = _patch_exec(monkeypatch, _FakeProcess(b'2\n'))
    assert asyncio.run(_monitor(tmp_path)._read_systemd_restarts()) == 2
    assert calls == [('systemctl', 'show', 'shopbot', '--property=NRestarts', '--value')]


@pytest.mark.parametrize(
    'result',
    [_FakeProcess(b'', returncode=1), _FakeProcess(b'[not set]\n'), FileNotFoundError('systemctl')],
)
def test_unreadable_counter_is_skipped_not_fatal(tmp_path, monkeypatch, result):
    _patch_exec(monkeypatch, result)
    assert asyncio.run(_monitor(tmp_path)._count_restarts_last_hour()) is None


def test_restarts_counted_as_growth_within_last_hour(tmp_path):
    monitor = _monitor(tmp_path)
    assert monitor._restarts_in_window(1_000, 0) == 0
    assert monitor._restarts_in_window(2_000, 3) == 3
    assert monitor._restarts_in_window(4_000, 5) == 5
    # Окно сдвинулось за выборку 1000: якорем стала выборка 2000 со счётчиком 3.
    assert monitor._restarts_in_window(6_000, 6) == 3
    assert monitor._restarts_in_window(20_000, 6) == 0


def test_samples_survive_process_restart(tmp_path):
    _monitor(tmp_path)._restarts_in_window(1_000, 4)
    assert _monitor(tmp_path)._restarts_in_window(1_300, 7) == 3


def test_manual_restart_resets_counter(tmp_path):
    monitor = _monitor(tmp_path)
    monitor._restarts_in_window(1_000, 9)
    assert monitor._restarts_in_window(1_300, 1) == 1


def test_corrupted_samples_file_is_ignored(tmp_path):
    monitor = _monitor(tmp_path)
    Path(monitor.restart_samples_path).write_text('{oops', encoding='utf-8')
    assert monitor._restarts_in_window(1_000, 2) == 0
