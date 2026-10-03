"""Tests for module.hang_dump: on-demand stack dump of a running Alas instance.

A hung instance can only be diagnosed from its Python stacks, and `sample`/lsof only show native frames. With the
handler installed, `kill -USR1 <pid>` writes every thread's Python stack to log/faulthandler-<config>-<pid>.txt and
the process keeps running. The file is written through its own file descriptor, not through the logger, so it also
works when the logging path is what is stuck.

Everything is tested with a real child process and a real signal.
"""
import ast
import os
import signal
import subprocess
import sys
import textwrap
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# The child parks a second thread in a recognisable function, so the dump can be checked for "all threads".
CHILD_WITH_HANDLER = textwrap.dedent('''
    import sys, threading, time
    from module.hang_dump import install_hang_dump

    def stuck_here():
        time.sleep(60)

    threading.Thread(target=stuck_here, daemon=True).start()
    print(install_hang_dump('alas', log_dir=sys.argv[1]), flush=True)
    time.sleep(60)
''')

CHILD_WITHOUT_HANDLER = textwrap.dedent('''
    import time
    print('ready', flush=True)
    time.sleep(60)
''')


@pytest.fixture
def spawn():
    procs = []

    def _spawn(code, *args):
        proc = subprocess.Popen([sys.executable, '-c', code, *args], cwd=ROOT, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        procs.append(proc)
        first_line = proc.stdout.readline().strip()
        if not first_line:
            proc.wait(timeout=10)
            pytest.fail(f'child printed nothing, stderr: {proc.stderr.read()[-500:]}')
        return proc, first_line

    yield _spawn
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _calls_in(path, function_name):
    """Names of everything called inside `function_name` (any class), parsed from source without importing it."""
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return {call.func.id for call in ast.walk(node)
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)}
    raise AssertionError(f'{function_name}() not found in {path}')


class TestWiring:
    def test_instance_entrypoint_installs_the_handler(self):
        # run_process is where every Alas instance process starts. Importing it pulls in the whole web UI, so this
        # reads its source instead. A merge from upstream that drops the call would otherwise go unnoticed.
        calls = _calls_in(os.path.join(ROOT, 'module', 'webui', 'process_manager.py'), 'run_process')

        assert 'install_hang_dump' in calls


class TestHangDump:
    def test_without_a_handler_sigusr1_kills_the_process(self, spawn):
        # Positive control: proves that in this environment the signal is delivered and is lethal by default,
        # which is what makes "the process survived" in the next test mean something.
        proc, _ = spawn(CHILD_WITHOUT_HANDLER)

        os.kill(proc.pid, signal.SIGUSR1)

        assert _wait_for(lambda: proc.poll() is not None)

    def test_sigusr1_dumps_every_thread_and_the_process_survives(self, spawn, tmp_path):
        proc, path = spawn(CHILD_WITH_HANDLER, str(tmp_path))

        os.kill(proc.pid, signal.SIGUSR1)

        def read():
            with open(path) as f:
                return f.read()

        # faulthandler writes the threads one after another, so wait for both headers, not for the first sign of
        # output. One header per thread (main + the parked one); which of them is labelled "Current" depends on which
        # thread the OS delivers the signal to, so only the number of headers is asserted.
        dumped = _wait_for(lambda: read().count('most recent call first') >= 2)
        content = read()
        assert dumped, content
        assert proc.poll() is None
        assert 'stuck_here' in content  # the parked thread, by its Python function name

    def test_ready_marker_names_the_pid_before_any_signal(self, spawn, tmp_path):
        # The external watchdog only signals instances that left this marker, because without the handler
        # SIGUSR1 would kill the instance.
        proc, path = spawn(CHILD_WITH_HANDLER, str(tmp_path))

        assert os.path.basename(path) == f'faulthandler-alas-{proc.pid}.txt'
        with open(path) as f:
            first_line = f.readline()
        assert first_line.startswith(f'# hang-dump ready pid={proc.pid} ')
