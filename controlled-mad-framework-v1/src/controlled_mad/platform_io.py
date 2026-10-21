"""跨平台单写者锁和尽力目录同步。"""
import contextlib
import errno
import os

if os.name=='nt':
    import msvcrt
else:
    import fcntl

class LockBusy(RuntimeError): pass

@contextlib.contextmanager
def exclusive_lock(stream):
    if os.name=='nt':
        stream.seek(0)
        if not stream.read(1):
            stream.seek(0); stream.write(b'0'); stream.flush(); os.fsync(stream.fileno())
        stream.seek(0)
        try: msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as exc:
            winerror=getattr(exc,'winerror',None)
            if winerror in (33,36) or (winerror is None and exc.errno in (errno.EACCES,errno.EDEADLK)):
                raise LockBusy from exc
            raise
        try: yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
    else:
        try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc: raise LockBusy from exc
        try: yield
        finally: fcntl.flock(stream,fcntl.LOCK_UN)

def sync_directory(path):
    if os.name=='nt': return
    fd=os.open(path,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
