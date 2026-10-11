"""Special files must refuse without opening a blocking byte stream."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize('reader', ['hash', 'companion', 'dependency', 'companion-race', 'dependency-race'])
def should_refuse_fifo_without_waiting_for_a_writer(tmp_path, reader):
    # given a FIFO, or a regular file replaced after its first image was sampled
    path = tmp_path / 'input'
    if reader.endswith('-race'):
        path.write_text('original bytes')
    else:
        os.mkfifo(path)
    script = r'''
import os, sys
from codex_review import context, images, projections
path, reader = sys.argv[1:]
if reader.endswith('-race'):
    original = images.image_of
    def swap(path):
        before = original(path)
        os.unlink(path)
        os.mkfifo(path)
        return before
    images.image_of = swap
try:
    if reader == 'hash':
        images.sha256_file(path)
    elif reader.startswith('companion'):
        context.capture_companions([dict(id='input', path=path)], None)
    else:
        root = dict(id='input', path=path, exclude=[])
        context.dependency_manifest(root, [root])
except projections.CaptureRefused as error:
    assert error.code == ('companion-unreadable' if reader.startswith('companion') else 'dependency-unreadable')
except OSError:
    assert reader == 'hash'
else:
    raise AssertionError('special file was accepted')
'''
    # when capture reads the input, then it refuses promptly with no writer or background child
    result = subprocess.run([sys.executable, '-c', script, str(path), reader],
                            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src')),
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr


def should_reject_fifo_replacing_a_file_between_stat_and_open(tmp_path, monkeypatch):
    from codex_review import images
    # given a regular file whose type changes after the preflight stat
    path = tmp_path / 'input'
    path.write_bytes(b'original')
    original_open = os.open
    def swap_open(name, flags):
        path.unlink()
        os.mkfifo(path)
        assert flags & os.O_NONBLOCK
        return original_open(name, flags)
    monkeypatch.setattr(os, 'open', swap_open)
    # when opening, then descriptor validation refuses the FIFO without reading or waiting
    with pytest.raises(OSError, match='not a regular file'):
        images.sha256_file(path)


def should_preserve_regular_hashes_and_symlink_images(tmp_path):
    from codex_review import images
    # given a regular byte stream and a final symlink
    path = tmp_path / 'input'
    path.write_bytes(b'known bytes')
    link = tmp_path / 'link'
    link.symlink_to('input')
    # when sampling, then content hashing and link-text identity remain distinct
    assert images.sha256_file(path) == images.sha256_bytes(b'known bytes')
    assert images.image_of(link)['content_identity'] == 'sha256:' + images.sha256_bytes(b'input')
    assert images.image_of(link)['kind'] == images.KIND_SYMLINK
