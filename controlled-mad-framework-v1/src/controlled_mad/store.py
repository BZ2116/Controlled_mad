"""不可覆盖对象与事件日志；单写者锁不会因进程退出留下占用。

两类锁分工明确，不可混用：
- 本文件的 ``_mutex`` 保护进程内多线程并发（execute_ready 的题级工作线程）。可重入，因为
  task_state→task_events→_ensure_events 一类调用天然嵌套。
- ``lock()`` 的跨进程文件锁只由主线程在 ``execute_ready`` 入口取一次。工作线程绝不能进入
  ``lock()``：msvcrt.locking 按文件描述符加锁，同进程另一个描述符会立刻 LockBusy。
"""
import contextlib
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from datetime import datetime, timezone
from .identity import canonical_bytes, canonical_hash, safe_id
from .platform_io import exclusive_lock, sync_directory, LockBusy

class IntegrityError(RuntimeError): pass
COLLECTIONS=('tasks','raw','results','parses','events','snapshots','materials','comparisons','messages','deliveries','inputs','analysis_inputs','exports','reports','attempts')

class RunStore:
    def __init__(self,path):
        self.path=Path(path).resolve()
        self._event_cache=None
        self._mutex=threading.RLock()
        self._checkpoint_sequence=0; self._last_checkpoint_time=time.monotonic()
        self.config=self.get('','manifest')['config']
    @classmethod
    def create(cls,root,run_id,config,**metadata):
        safe_id(run_id)
        path=Path(root)/run_id
        path.mkdir(parents=True,exist_ok=False)
        for name in COLLECTIONS: (path/name).mkdir()
        obj=object.__new__(cls); obj.path=path.resolve(); obj.config=config; obj._event_cache=None
        obj._mutex=threading.RLock()
        obj._checkpoint_sequence=0; obj._last_checkpoint_time=time.monotonic()
        obj.put('','manifest',{'run_id':run_id,'config':config,'config_hash':canonical_hash(config),**metadata})
        return obj
    @contextlib.contextmanager
    def lock(self):
        with (self.path/'.writer.lock').open('a+b') as f:
            try:
                with exclusive_lock(f):
                    self._event_cache=None
                    try: yield self
                    finally: self.save_checkpoint()
            except LockBusy: raise IntegrityError('本运行已有写入进程') from None
    def _file(self,collection,key,suffix='.json'):
        if collection not in COLLECTIONS+('',): raise ValueError('未知对象类型')
        safe_id(key)
        return self.path/collection/(key+suffix)
    def write_blob(self,collection,key,data,suffix='.json'):
        # 互斥必须覆盖 exists() 与 open('xb') 之间：并发写同键时先到者落盘，
        # 后到者若漏看文件会撞 FileExistsError 而非走幂等分支。
        with self._mutex:
            p=self._file(collection,key,suffix)
            marker=Path(str(p)+'.committed')
            digest=hashlib.sha256(data).hexdigest()
            if p.exists():
                if not marker.exists(): raise IntegrityError(f'未提交文件需要检查：{p.name}')
                if marker.read_text(encoding='ascii')!=digest or p.read_bytes()!=data: raise IntegrityError(f'不可覆盖对象冲突：{p.name}')
                return key
            with p.open('xb') as f: f.write(data); f.flush(); os.fsync(f.fileno())
            with marker.open('x',encoding='ascii') as f: f.write(digest); f.flush(); os.fsync(f.fileno())
            sync_directory(p.parent)
            return key
    def read_blob(self,collection,key,suffix='.json'):
        p=self._file(collection,key,suffix); marker=Path(str(p)+'.committed')
        data=p.read_bytes()
        try: digest=marker.read_text(encoding='ascii')
        except FileNotFoundError: raise IntegrityError(f'提交标记或哈希错误：{p.name}') from None
        if digest!=hashlib.sha256(data).hexdigest(): raise IntegrityError(f'提交标记或哈希错误：{p.name}')
        return data
    def put(self,collection,key,obj): return self.write_blob(collection,key,canonical_bytes(obj))
    def get(self,collection,key): return json.loads(self.read_blob(collection,key))
    def ids(self,collection): return sorted(p.stem for p in (self.path/collection).glob('*.json'))
    def _ensure_events(self,progress=None,use_checkpoint=True):
        # 惰性建索引不是线程安全的：两个线程会各自重建并丢失对方的增量。
        with self._mutex:
            if self._event_cache is not None: return
            keys=self.ids('events')
            if any(key!=f'{index:09d}' for index,key in enumerate(keys,1)):
                raise IntegrityError('历史事件序列存在缺口，不能自动覆盖或从头重跑')
            from .checkpoint import load_checkpoint, CheckpointMismatch
            try: records,prefix=load_checkpoint(self,keys) if use_checkpoint else ([],0)
            except CheckpointMismatch as exc: raise IntegrityError(str(exc)) from None
            self._checkpoint_sequence=prefix
            last=0
            if progress: progress({'loaded':prefix,'total':len(keys),'source':'checkpoint' if prefix else 'legacy_events'})
            remaining=keys[prefix:]
            # 仅首次迁移旧运行/丢失缓存时回放事件；并行读取，避免逐文件串行等待。
            if len(remaining)>64:
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(max_workers=8) as pool:
                    for offset in range(0,len(remaining),256):
                        batch=remaining[offset:offset+256]
                        records.extend(pool.map(lambda key:self.get('events',key),batch))
                        now=time.monotonic()
                        if progress and (now-last>=0.5 or offset+len(batch)==len(remaining)):
                            progress({'loaded':len(records),'total':len(keys),'source':'legacy_events' if not prefix else 'checkpoint'})
                            last=now
            else:
                for key in remaining: records.append(self.get('events',key))
            if any(e.get('sequence')!=index for index,e in enumerate(records,1)):
                raise IntegrityError('事件内容序号与文件名不一致')
            self._event_cache=records
            self._selected={}; self._starts={}; self._finished=set(); self._retry={}
            self._budget_starts={}; self._usage={}; self._stages={}; self._tokens=0; self._unknown_usage=0
            for e in self._event_cache: self._index_event(e)
    def save_checkpoint(self):
        """仅写可替换缓存；原事件、原始响应和派生结果不修改。"""
        with self._mutex:
            if self._event_cache is None or len(self._event_cache)==self._checkpoint_sequence: return
            from .checkpoint import write_checkpoint
            if write_checkpoint(self,self._event_cache):
                self._checkpoint_sequence=len(self._event_cache)
                self._last_checkpoint_time=time.monotonic()
    def _index_event(self,e):
        # 只由持锁路径调用：就地累加预算计数器，不是并发安全的。
        kind=e['kind']
        if kind=='selected': self._selected[e['task_id']]=e['parse_id']
        elif kind=='retry_requested': self._retry[e['task_id']]=e
        elif kind=='finished': self._finished.add(e['attempt_id'])
        elif kind=='started':
            self._starts.setdefault(e['task_id'],[]).append(e)
            aid=e['attempt_id']
            if aid not in self._budget_starts:
                self._budget_starts[aid]=e
                self._stages[e['stage']]=self._stages.get(e['stage'],0)+1
                self._tokens+=self._usage.get(aid,e['reserved_tokens'])
                self._unknown_usage+=int(aid not in self._usage)
        elif kind=='usage':
            aid=e['attempt_id']
            if aid in self._budget_starts:
                self._tokens+=e['tokens']-self._usage.get(aid,self._budget_starts[aid]['reserved_tokens'])
                self._unknown_usage-=int(aid not in self._usage)
            self._usage[aid]=e['tokens']
    def budget_totals(self):
        self._ensure_events()
        with self._mutex:
            return {'calls':len(self._budget_starts),'tokens':self._tokens,'stages':dict(self._stages),'unknown_usage':self._unknown_usage,'cost':None}
    def task_events(self,task_id):
        self._ensure_events()
        with self._mutex:
            starts=self._starts.get(task_id,[])
            return starts,self._retry.get(task_id),bool(starts and starts[-1]['attempt_id'] in self._finished)
    def selected_parse(self,task_id):
        self._ensure_events()
        with self._mutex:
            return self._selected.get(task_id)
    def all(self,collection):
        if collection=='events':
            self._ensure_events()
            with self._mutex: return list(self._event_cache)
        return [self.get(collection,k) for k in self.ids(collection)]
    def event(self,kind,**data):
        # 序号分配、落盘、缓存追加、预算索引必须是一个原子步骤，否则并发下会撞序号。
        with self._mutex:
            self._ensure_events(); sequence=self._event_cache[-1]['sequence']+1 if self._event_cache else 1
            record={'sequence':sequence,'kind':kind,'time':datetime.now(timezone.utc).isoformat(),**data}
            self.put('events',f'{sequence:09d}',record)
            self._event_cache.append(record)
            self._index_event(record)
            if time.monotonic()-self._last_checkpoint_time>=300: self.save_checkpoint()
    def selection(self):
        self._ensure_events()
        with self._mutex: return dict(self._selected)
    def result(self,task_id):
        parse_id=self.selected_parse(task_id)
        if not parse_id: return None
        p=self.get('parses',parse_id)
        return p if p['valid'] else None
    def attempts(self,task_id):
        self._ensure_events()
        with self._mutex: return list(self._starts.get(task_id,[]))
    def verify(self,progress=None):
        count=0; started=time.monotonic(); last=0
        def report(collection,force=False):
            nonlocal last
            now=time.monotonic()
            if progress and (force or now-last>=0.5):
                progress({'collection':collection,'verified_objects':count,'elapsed':round(now-started,1)})
                last=now
        for col in ('',)+COLLECTIONS:
            if col=='': self.get('','manifest'); count+=1; continue
            report(col,True)
            with os.scandir(self.path/col) as entries:
                for entry in entries:
                    if entry.is_file() and not entry.name.endswith('.committed'):
                        report(col)
                        suffix=Path(entry.name).suffix
                        self.read_blob(col,entry.name[:-len(suffix)],suffix); count+=1
            report(col,True)
        expected=canonical_hash(self.config)
        if self.get('','manifest')['config_hash']!=expected: raise IntegrityError('配置冻结哈希不匹配')
        return {'verified_objects':count,'config_hash':expected}
