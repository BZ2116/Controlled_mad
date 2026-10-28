"""可丢弃的断点缓存；原始事件仍是唯一事实来源。"""
import gzip
import json
import os
import uuid
import zlib
from .identity import canonical_bytes, canonical_hash
from .platform_io import sync_directory

NAME='.resume-checkpoint.json.gz'


class CheckpointMismatch(ValueError): pass


def load_checkpoint(store,keys):
    try:
        payload=json.loads(gzip.decompress((store.path/NAME).read_bytes()))
        events=payload['events']; sequence=payload['last_sequence']
        if (payload['schema']!=1 or payload['config_hash']!=canonical_hash(store.config)
            or not isinstance(events,list) or type(sequence) is not int or sequence!=len(events)
            or any(not isinstance(e,dict) or e.get('sequence')!=i for i,e in enumerate(events,1))):
            return [],0
    except (OSError,EOFError,ValueError,TypeError,KeyError,zlib.error):
        return [],0
    if sequence>len(keys):
        raise CheckpointMismatch('断点引用的历史事件缺失，不能把旧运行当成新运行')
    return events,sequence


def write_checkpoint(store,events):
    payload={'schema':1,'config_hash':canonical_hash(store.config),
             'last_sequence':len(events),'events':events}
    data=gzip.compress(canonical_bytes(payload),compresslevel=1,mtime=0)
    temporary=store.path/f'.resume-checkpoint-{uuid.uuid4().hex}.tmp'
    try:
        with temporary.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary,store.path/NAME)
        sync_directory(store.path)
        return True
    except OSError:
        # 缓存失败不能回滚已提交事件；旧缓存和日志尾部仍可恢复。
        return False
    finally:
        try: temporary.unlink(missing_ok=True)
        except OSError: pass
