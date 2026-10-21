"""不可覆盖对象与事件日志；单写者锁不会因进程退出留下占用。"""
import contextlib
import hashlib
import json
import os
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
        self.config=self.get('','manifest')['config']
    @classmethod
    def create(cls,root,run_id,config,**metadata):
        safe_id(run_id)
        path=Path(root)/run_id
        path.mkdir(parents=True,exist_ok=False)
        for name in COLLECTIONS: (path/name).mkdir()
        obj=object.__new__(cls); obj.path=path.resolve(); obj.config=config; obj._event_cache=None
        obj.put('','manifest',{'run_id':run_id,'config':config,'config_hash':canonical_hash(config),**metadata})
        return obj
    @contextlib.contextmanager
    def lock(self):
        with (self.path/'.writer.lock').open('a+b') as f:
            try:
                with exclusive_lock(f):
                    self._event_cache=None
                    yield self
            except LockBusy: raise IntegrityError('本运行已有写入进程') from None
    def _file(self,collection,key,suffix='.json'):
        if collection not in COLLECTIONS+('',): raise ValueError('未知对象类型')
        safe_id(key)
        return self.path/collection/(key+suffix)
    def write_blob(self,collection,key,data,suffix='.json'):
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
        if not p.exists(): raise FileNotFoundError(p)
        data=p.read_bytes()
        if not marker.exists() or marker.read_text(encoding='ascii')!=hashlib.sha256(data).hexdigest(): raise IntegrityError(f'提交标记或哈希错误：{p.name}')
        return data
    def put(self,collection,key,obj): return self.write_blob(collection,key,canonical_bytes(obj))
    def get(self,collection,key): return json.loads(self.read_blob(collection,key))
    def ids(self,collection): return sorted(p.stem for p in (self.path/collection).glob('*.json'))
    def _ensure_events(self):
        if self._event_cache is not None: return
        self._event_cache=[self.get('events',k) for k in self.ids('events')]
        self._selected={}; self._starts={}; self._finished=set(); self._retry={}
        self._budget_starts={}; self._usage={}; self._stages={}; self._tokens=0; self._unknown_usage=0
        for e in self._event_cache: self._index_event(e)
    def _index_event(self,e):
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
        return {'calls':len(self._budget_starts),'tokens':self._tokens,'stages':dict(self._stages),'unknown_usage':self._unknown_usage,'cost':None}
    def task_events(self,task_id):
        self._ensure_events()
        starts=self._starts.get(task_id,[])
        return starts,self._retry.get(task_id),bool(starts and starts[-1]['attempt_id'] in self._finished)
    def selected_parse(self,task_id):
        self._ensure_events()
        return self._selected.get(task_id)
    def all(self,collection):
        if collection=='events':
            self._ensure_events()
            return list(self._event_cache)
        return [self.get(collection,k) for k in self.ids(collection)]
    def event(self,kind,**data):
        self._ensure_events(); sequence=self._event_cache[-1]['sequence']+1 if self._event_cache else 1
        record={'sequence':sequence,'kind':kind,'time':datetime.now(timezone.utc).isoformat(),**data}
        self.put('events',f'{sequence:09d}',record)
        self._event_cache.append(record)
        self._index_event(record)
    def selection(self):
        self._ensure_events()
        return dict(self._selected)
    def result(self,task_id):
        parse_id=self.selected_parse(task_id)
        if not parse_id: return None
        p=self.get('parses',parse_id)
        return p if p['valid'] else None
    def attempts(self,task_id):
        self._ensure_events()
        return list(self._starts.get(task_id,[]))
    def verify(self):
        count=0
        for col in ('',)+COLLECTIONS:
            if col=='': self.get('','manifest'); count+=1; continue
            for p in (self.path/col).iterdir():
                if p.is_file() and not p.name.endswith('.committed'):
                    suffix=p.suffix
                    self.read_blob(col,p.name[:-len(suffix)],suffix); count+=1
        expected=canonical_hash(self.config)
        if self.get('','manifest')['config_hash']!=expected: raise IntegrityError('配置冻结哈希不匹配')
        return {'verified_objects':count,'config_hash':expected}
