"""Per-invocation durable logs, launch evidence and locally attributable timing."""
import json
import os
import socket
import subprocess
import sys
import time
import uuid

from codex_review import images, response, state


def nonlaunch_evidence(directory, cwd):
    marker = os.path.join(directory, 'not-launched.json')
    if not os.path.isfile(marker):
        return None
    reservation = os.path.join(directory, 'reservation.json')
    with open(marker, encoding='utf-8') as handle:
        failure = json.load(handle)
    with open(reservation, encoding='utf-8') as handle:
        reserved = json.load(handle)
    response._require(isinstance(failure, dict) and isinstance(failure.get('error'), str) and failure['error'].strip() and
                      isinstance(reserved, dict) and reserved.get('cwd') == cwd and
                      isinstance(reserved.get('host'), str) and bool(reserved['host']) and
                      isinstance(reserved.get('argv'), list) and bool(reserved['argv']) and
                      all(isinstance(value, str) for value in reserved['argv']),
                      'invalid-nonlaunch-evidence', 'nonlaunch needs a failure and matching reservation')
    response._require(not any(os.path.exists(os.path.join(directory, name)) for name in
                              ('launch.json', 'session.json', 'last-message.txt')) and
                      all(os.path.isfile(os.path.join(directory, name)) and
                          os.path.getsize(os.path.join(directory, name)) == 0 for name in ('stdout.jsonl', 'stderr.txt')),
                      'contradictory-nonlaunch-evidence', 'launch or output evidence contradicts nonlaunch')
    return dict(marker_sha256=images.sha256_file(marker), reservation_sha256=images.sha256_file(reservation))


def _process_identity(pid):
    """Sample kernel start identity and termination state together where available."""
    try:
        if sys.platform == 'darwin':
            import ctypes
            class BSDInfo(ctypes.Structure):
                # SDK sys/proc_info.h: proc_bsdinfo / PROC_PIDTBSDINFO.
                _fields_ = [('prefix', ctypes.c_uint32 * 12), ('comm', ctypes.c_char * 16),
                            ('name', ctypes.c_char * 32), ('suffix', ctypes.c_uint32 * 6),
                            ('seconds', ctypes.c_uint64), ('microseconds', ctypes.c_uint64)]
            info = BSDInfo()
            library = ctypes.CDLL('/usr/lib/libproc.dylib')
            library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                                             ctypes.c_void_p, ctypes.c_int]
            size = library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
            if size == ctypes.sizeof(info):
                return '{}:{}'.format(info.seconds, info.microseconds), None
        elif sys.platform.startswith('linux'):
            with open('/proc/{}/stat'.format(pid), encoding='ascii') as handle:
                fields = handle.read().rsplit(')', 1)[1].split()
            with open('/proc/sys/kernel/random/boot_id', encoding='ascii') as handle:
                boot = handle.read().strip()
            return boot + ':' + fields[19], fields[0]
    except (OSError, ValueError, IndexError):
        pass
    return None, None


def process_start(pid):
    return _process_identity(pid)[0]


def process_state(launch):
    if not launch or launch.get('host') != socket.gethostname() or type(launch.get('pid')) is not int:
        return 'unknown'
    pid = launch['pid']
    if pid <= 0:
        return 'unknown'
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return 'absent'
    except PermissionError:
        return 'unknown'
    observed, kernel_state = _process_identity(pid)
    if not observed or not launch.get('process_start'):
        return 'unknown'
    if observed != launch['process_start']:
        return 'reused'
    return 'exited' if kernel_state in ('Z', 'X', 'x') else 'live'


class _ThreadLog:
    def __init__(self):
        self.offset = 0
        self.pending = b''
        self.identities = set()

    def read(self, log_path, *, final=False):
        with open(log_path, 'rb') as handle:
            if os.fstat(handle.fileno()).st_size < self.offset:
                self.offset, self.pending, self.identities = 0, b'', set()
            handle.seek(self.offset)
            data = handle.read()
            self.offset = handle.tell()
        if data or final:
            lines = (self.pending + data).split(b'\n')
            self.pending = lines.pop()
            if final and self.pending:
                lines.append(self.pending)
                self.pending = b''
            for line in lines:
                try:
                    event = json.loads(line)
                    if event.get('type') == 'thread.started':
                        self.identities.add(str(uuid.UUID(event['thread_id'])))
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
        return next(iter(self.identities)) if len(self.identities) == 1 else None


def _thread(log_path):
    return _ThreadLog().read(log_path, final=True)


def launch(argv, directory, cwd, *, env=None):
    stdout_path = os.path.join(directory, 'stdout.jsonl')
    stderr_path = os.path.join(directory, 'stderr.txt')
    started = time.time()
    timing = dict(wall_seconds=None, cpu_user_seconds=None, cpu_system_seconds=None,
                  method='wait4' if hasattr(os, 'wait4') else 'unavailable',
                  process_scope='reviewer child and descendants it waited for', complete=False)
    process = None
    returncode = None
    thread_id = None
    failure = None
    with open(stdout_path, 'wb', buffering=0) as output, open(stderr_path, 'wb', buffering=0) as errors:
        state.atomic_json(os.path.join(directory, 'reservation.json'),
                          dict(host=socket.gethostname(), cwd=cwd, argv=argv, reserved_at=started))
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output, stderr=errors, stdin=subprocess.DEVNULL)
        except OSError as error:
            failure = str(error)
            state.atomic_json(os.path.join(directory, 'not-launched.json'), {'error': failure})
        if process is not None:
            state.atomic_json(os.path.join(directory, 'launch.json'),
                              dict(host=socket.gethostname(), pid=process.pid, started_at=time.time(),
                                   process_start=process_start(process.pid),
                                   cwd=cwd, argv=argv, stdout=stdout_path, stderr=stderr_path))
            log_reader = _ThreadLog()
            while True:
                os.fsync(output.fileno())
                os.fsync(errors.fileno())
                observed = log_reader.read(stdout_path)
                if observed != thread_id:
                    thread_id = observed
                    state.atomic_json(os.path.join(directory, 'session.json'),
                                      dict(thread_id=thread_id, cwd=cwd, sandbox='read-only'))
                if hasattr(os, 'wait4'):
                    pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                    if pid:
                        returncode = os.waitstatus_to_exitcode(status)
                        process.returncode = returncode
                        timing.update(cpu_user_seconds=usage.ru_utime, cpu_system_seconds=usage.ru_stime,
                                      complete=True)
                        break
                else:
                    returncode = process.poll()
                    if returncode is not None:
                        break
                time.sleep(0.1)
            os.fsync(output.fileno())
            os.fsync(errors.fileno())
            thread_id = log_reader.read(stdout_path, final=True)
            state.atomic_json(os.path.join(directory, 'session.json'),
                              dict(thread_id=thread_id, cwd=cwd, sandbox='read-only'))
    completed_at = time.time()
    timing['wall_seconds'] = round(completed_at - started, 6)
    completion = dict(launched=process is not None, returncode=returncode, thread_id=thread_id,
                      timing=timing, error=failure, completed_at=completed_at)
    state.atomic_json(os.path.join(directory, 'completion.json'), completion)
    return completion
