"""On-demand Python stack dump for a running Alas instance.

`kill -USR1 <pid>` writes the stack of every thread to log/faulthandler-<config>-<pid>.txt and the process keeps
running. The dump goes through its own file descriptor, not through the logger, so it works even when logging is
what is stuck. The first line of the file is a marker that tells outside tools the handler is installed, which
matters because SIGUSR1 kills a process that has no handler.
"""
import datetime
import faulthandler
import os
import signal

# faulthandler keeps only the raw file descriptor, so the file objects must stay referenced.
_dump_files = {}


def install_hang_dump(config_name, log_dir='./log'):
    """
    Args:
        config_name (str):
        log_dir (str):

    Returns:
        str | None: Path of the dump file, or None if the platform has no SIGUSR1 (Windows).
    """
    sig = getattr(signal, 'SIGUSR1', None)
    if sig is None:
        return None

    os.makedirs(log_dir, exist_ok=True)
    pid = os.getpid()
    path = os.path.join(log_dir, f'faulthandler-{config_name}-{pid}.txt')
    file = open(path, mode='w', encoding='utf-8')
    file.write(f'# hang-dump ready pid={pid} at {datetime.datetime.now().isoformat(timespec="seconds")}\n')
    file.flush()
    faulthandler.register(sig, file=file, all_threads=True)
    _dump_files[config_name] = file
    return path
