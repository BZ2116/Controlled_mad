"""追加式逐题调度变更；不改冻结配置、材料资格或已有实验对象。"""
from collections import Counter
import hashlib
from pathlib import Path
from .identity import canonical_hash

SCHEDULE = 'execution-schedule'


def item_pipeline_enabled(store):
    if not (store.path / 'inputs' / f'{SCHEDULE}.json').exists(): return False
    record = store.get('inputs', SCHEDULE)
    expected=schedule_record(store)
    if any(record.get(key)!=value for key,value in expected.items()) or set(record)-set(expected)-{'implementation'}:
        raise ValueError('逐题调度记录与冻结配置不一致')
    return True


def schedule_record(store):
    return {'mode': 'item_pipeline_v1', 'config_hash': canonical_hash(store.config),
            'material_qualification': 'unchanged',
            'change': 'user_requested_per_item_freeze_instead_of_global_start_barrier'}


def enable_item_pipeline(store):
    """调用者必须持运行锁；原配置和所有原始记录保持不变。"""
    if not store.config.get('material_gate'):
        raise ValueError('逐题材料冻结仅适用于已启用材料资格屏障的运行')
    if item_pipeline_enabled(store): return
    implementation={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                    for name in ('pipeline.py','scheduler.py','engine.py','runner.py','store.py','cli.py')}
    store.put('inputs', SCHEDULE, {**schedule_record(store),'implementation':implementation})


def freeze_item_materials(store, context, materials, persist=True):
    """本题全部材料确定后固定资格；重复构建只允许相同结果。"""
    record = {key: context[key] for key in ('dataset_id', 'item_id', 'model_id', 's0_hash')}
    record.update(material_ids=sorted(m['material_id'] for m in materials),
                  counts=dict(Counter(m['status'] for m in materials)))
    key = 'item-material-freeze-' + canonical_hash([context[k] for k in ('dataset_id', 'item_id', 'model_id')])
    if (store.path / 'inputs' / f'{key}.json').exists():
        if store.get('inputs', key) != record:
            raise ValueError('本题材料已偏离冻结资格，不能在原运行重新入组')
    elif persist:
        store.put('inputs', key, record)

