"""显式启用的配置矩阵；待定数据/模型不伪装成已完成复核。"""
import json
from pathlib import Path
from .identity import safe_id


def replication_plan(path):
    from .api import plan
    path = Path(path).resolve()
    matrix = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(matrix, dict) or set(matrix) != {'slots'} or not isinstance(matrix['slots'], list):
        raise ValueError('复核矩阵必须含 slots 数组')
    enabled = []; pending = []; identifiers = set()
    for slot in matrix['slots']:
        if not isinstance(slot, dict) or set(slot) != {'id', 'enabled', 'config', 'pending'} or type(slot['enabled']) is not bool:
            raise ValueError('复核槽位字段不符')
        safe_id(slot['id'])
        if slot['id'] in identifiers: raise ValueError('复核槽位 ID 重复')
        identifiers.add(slot['id'])
        if slot['enabled']:
            if not isinstance(slot['config'], str) or not slot['config'].strip() or slot['pending']:
                raise ValueError('启用槽位必须提供真实配置且没有待定项')
            config_path = path.parent / slot['config']
            enabled.append({'id': slot['id'], 'config': str(config_path.resolve()), 'plan': plan(config_path)})
        else:
            if not isinstance(slot['pending'], str) or not slot['pending'].strip(): raise ValueError('未启用槽位须说明待定内容')
            pending.append(slot)
    return {'enabled': enabled, 'pending': pending, 'total_tasks_upper': sum(r['plan']['total_tasks_upper'] for r in enabled)}
