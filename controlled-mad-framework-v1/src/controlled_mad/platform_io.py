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
        # 必须先加锁再初始化字节：msvcrt.locking 对全部句柄锁同一字节区间，
        # 已被别人锁住时 read() 会直接 PermissionError，根本走不到下面转 LockBusy 的分支。
        # 长度改用 fstat 查，字节区间锁不影响元数据查询。
        stream.seek(0)
        try: msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as exc:
            winerror=getattr(exc,'winerror',None)
            if winerror in (33,36) or (winerror is None and exc.errno in (errno.EACCES,errno.EDEADLK)):
                raise LockBusy from exc
            raise
        try:
            if os.fstat(stream.fileno()).st_size==0:
                stream.write(b'0'); stream.flush(); os.fsync(stream.fileno())
            yield
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
