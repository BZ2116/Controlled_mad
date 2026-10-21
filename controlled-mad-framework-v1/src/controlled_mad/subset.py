"""只用题号和固定种子选择补充实验题目。"""
import random
from .domain import DatasetBundle
from .identity import canonical_hash

def select_bundle(bundle, subset):
    by_id={item.item_id:item for item in bundle.items}
    if len(by_id)!=len(bundle.items): raise ValueError('跨数据源题号重复，无法按 item_id 选择')
    if 'item_ids' in subset:
        chosen=subset['item_ids']
        absent=set(chosen)-set(by_id)
        if absent: raise ValueError(f'子集题号不存在：{sorted(absent)[:5]}')
    else:
        size=subset['size']
        if size>len(by_id): raise ValueError('子集大小超过可用题目数')
        ids=sorted(by_id)
        chosen=random.Random(subset['seed']).sample(ids,size)
    chosen_set=set(chosen)
    items=tuple(item for item in bundle.items if item.item_id in chosen_set)
    gold=tuple(row for row in bundle.gold if row.item_id in chosen_set)
    return DatasetBundle(items,gold,bundle.source_hash,bundle.rejected), {'candidate_items':len(bundle.items),'selected_item_ids':[item.item_id for item in items],
        'selection_hash':canonical_hash([bundle.source_hash,subset,[item.item_id for item in items]])}
