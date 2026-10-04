"""Publication and live child behavior across actual local process interruption."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from codex_review import state, transport


def should_leave_the_previous_bytes_intact_if_atomic_publication_is_interrupted(tmp_path, monkeypatch):
    # given an existing record and an interrupted rename
    path = tmp_path / 'response.json'
    path.write_bytes(b'previous bytes')
    def fail(*args):
        raise OSError('interrupted before rename')
    monkeypatch.setattr(state.os, 'replace', fail)
    # when publishing new raw bytes, then neither a partial record nor a staging file survives
    with pytest.raises(OSError, match='interrupted'):
        state.atomic_bytes(str(path), b'new retained response')
    assert path.read_bytes() == b'previous bytes'
    assert list(tmp_path.iterdir()) == [path]


def should_keep_streamed_identity_and_late_output_when_the_driver_dies(tmp_path):
    # given a real driver and a child that writes a session before waiting independently
    attempt = tmp_path / 'attempt'
    attempt.mkdir()
    stop = tmp_path / 'stop'
    child_script = tmp_path / 'child.py'
    thread = 'a2c80d10-8dbb-4426-9089-f1d0b3a88e23'
    child_script.write_text(
        'import json, os, pathlib, sys, time\n'
        'print(json.dumps(dict(type="thread.started", thread_id=' + repr(thread) + ')), flush=True)\n'
        'os.fsync(sys.stdout.fileno())\n'
        'deadline = time.monotonic() + 15\n'
        'while not pathlib.Path(sys.argv[1]).exists() and time.monotonic() < deadline: time.sleep(.02)\n'
        'print(json.dumps(dict(type="turn.completed")), flush=True)\n'
        'os.fsync(sys.stdout.fileno())\n')
    source = str(Path(__file__).resolve().parents[2] / 'src')
    command = ('from codex_review import transport; import sys; '
               'transport.launch([sys.executable, sys.argv[2], sys.argv[3]], sys.argv[1], sys.argv[1])')
    driver = subprocess.Popen([sys.executable, '-c', command, str(attempt), str(child_script), str(stop)],
                              env=dict(os.environ, PYTHONPATH=source), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    def until(predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            assert time.monotonic() < deadline, 'local child control timed out'
            time.sleep(.02)
    launch = None
    try:
        until(lambda: (attempt / 'session.json').exists())
        launch = json.loads((attempt / 'launch.json').read_text())
        assert transport.process_state(launch) == 'live'
        # when the driver is killed, then the original child and durable streamed identity remain observable
        driver.kill()
        driver.wait(timeout=5)
        assert transport.process_state(launch) == 'live'
        assert json.loads((attempt / 'session.json').read_text())['thread_id'] == thread
        assert not (attempt / 'completion.json').exists()
        stop.touch()
        until(lambda: 'turn.completed' in (attempt / 'stdout.jsonl').read_text())
        until(lambda: transport.process_state(launch) in ('absent', 'reused'))
        assert transport._thread(str(attempt / 'stdout.jsonl')) == thread
        assert not (attempt / 'completion.json').exists()  # output alone is not a driver completion receipt
    finally:
        stop.touch()
        if driver.poll() is None:
            driver.kill()
        driver.wait(timeout=5)
        driver.stderr.close()
        if launch and transport.process_state(launch) == 'live':
            os.kill(launch['pid'], signal.SIGTERM)
