import json, hashlib
from pathlib import Path
from ..domain import Item, GoldRecord, DatasetBundle

def from_rows(rows, source_hash=''):
    items=[]; gold=[]; rejected=[]; seen=set()
    for index,row in enumerate(rows):
        ds=row.get('dataset_id','dataset'); iid=row.get('item_id',f'item_{index:04d}')
        if (ds,iid) in seen: raise ValueError(f'题号重复：{ds}/{iid}')
        seen.add((ds,iid))
        try:
            if not isinstance(row.get('evidence'),(list,tuple)) or not isinstance(row.get('labels'),(list,tuple)): raise ValueError('evidence 和 labels 必须是数组')
            metadata=row.get('metadata',{})
            if isinstance(metadata,dict): metadata=tuple(sorted((str(k),str(v)) for k,v in metadata.items() if v is not None))
            elif isinstance(metadata,(list,tuple)): metadata=tuple((str(k),str(v)) for k,v in metadata)
            else: raise ValueError('metadata 必须是对象或键值对数组')
            task_input=row.get('task_input','')
            item=Item(ds,iid,row.get('claim',''),tuple(row.get('evidence',[])),tuple(row.get('labels',[])),task_input,metadata)
            if not all(isinstance(x,str) for x in (ds,iid,item.claim)): raise ValueError('字段类型无效')
            if row.get('gold') is not None and row['gold'] not in item.labels: raise ValueError('金标不在原生集合')
            items.append(item)
            if row.get('gold') is not None: gold.append(GoldRecord(ds,iid,row['gold']))
        except (ValueError,TypeError,AttributeError) as e:
            rejected.append({'dataset_id':ds,'item_id':iid,'index':index,'reason':str(e)})
    return DatasetBundle(tuple(items),tuple(gold),source_hash,tuple(rejected))

class JsonlAdapter:
    def load(self,source):
        data=Path(source).read_bytes()
        rows=[json.loads(line) for line in data.decode('utf-8-sig').splitlines() if line.strip()]
        return from_rows(rows,hashlib.sha256(data).hexdigest())
