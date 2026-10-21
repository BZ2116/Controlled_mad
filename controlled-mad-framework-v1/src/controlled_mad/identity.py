import dataclasses
import hashlib
import json
import random
import re

def plain(value):
    if dataclasses.is_dataclass(value): return plain(dataclasses.asdict(value))
    if isinstance(value, dict): return {k:plain(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [plain(v) for v in value]
    return value

def canonical_bytes(value):
    return json.dumps(plain(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')

def canonical_hash(value): return hashlib.sha256(canonical_bytes(value)).hexdigest()

def select_sender(dataset_id, item_id, seed):
    rng=random.Random(int(canonical_hash([dataset_id,item_id,seed]),16))
    return f'agent_{rng.randrange(3)+1}'

def safe_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',value):
        raise ValueError('ID 只允许字母、数字、点、横线和下划线，且不能是路径')
    stem=value.split('.')[0].upper()
    if value.endswith('.') or stem in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}:
        raise ValueError('ID 不能使用 Windows 保留文件名或以点结尾')
    return value
