import hashlib
import json
from typing import Protocol
from pathlib import Path
from ..domain import DatasetBundle

class DatasetAdapter(Protocol):
    def load(self,source:Path)->DatasetBundle: ...

def read_records(source):
    path=Path(source)
    raw=path.read_bytes()
    try:
        decoded=json.loads(raw.decode('utf-8'))
    except json.JSONDecodeError:
        decoded=[json.loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    if isinstance(decoded,dict):
        if isinstance(decoded.get('data'),list): decoded=decoded['data']
        elif isinstance(decoded.get('records'),list): decoded=decoded['records']
        else: decoded=[decoded]
    if not isinstance(decoded,list) or any(not isinstance(row,dict) for row in decoded):
        raise ValueError('数据文件必须是 JSON 对象数组或 JSONL 对象')
    return raw,decoded

def source_digest(raw): return hashlib.sha256(raw).hexdigest()

def adapter_for(name):
    if name=='jsonl':
        from .jsonl import JsonlAdapter
        return JsonlAdapter()
    if name=='averitec':
        from .averitec import AveritecAdapter
        return AveritecAdapter()
    if name=='mmlu_pro':
        from .mmlu_pro import MMLUProAdapter
        return MMLUProAdapter()
    if name=='bbq':
        from .bbq import BBQAdapter
        return BBQAdapter()
    raise ValueError(f'未知数据适配器：{name}')
