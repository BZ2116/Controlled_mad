"""完整条件试运行：显式源结构和逐题真实终局矩阵，不用缺失值补裁决。"""
import re
from collections import Counter, defaultdict

OPERATIONS = ('ethos', 'logos', 'pathos', 'conclusion_front', 'conclusion_back',
              'support_first', 'limitation_first')
STRUCTURE = 'support_conclusion_limitation_support_v1'


def structured_blocks(original):
    parts = re.split(r'(\r?\n[ \t]*\r?\n)', original)
    headings = ('Support A:', 'Conclusion:', 'Limitation:', 'Support B:')
    if len(parts) != 7:
        raise ValueError('完整矩阵原文必须有四个独立段落：Support A / Conclusion / Limitation / Support B')
    blocks = []
    for index, (heading, kind) in enumerate(zip(headings, ('support', 'conclusion', 'limitation', 'support'))):
        text = parts[2*index]
        if not text.startswith(heading) or not text[len(heading):].strip():
            raise ValueError('完整矩阵段落标题或内容无效：' + heading)
        if index < 3: text += parts[2*index+1]
        blocks.append({'kind': kind, 'text': text})
    return blocks


def reorder_structured(original, operation, layout='slot_first_v1', normalize=False):
    """移动四段内容并复用原来的三个段落分隔符，避免末段移位后与下一段粘连。"""
    if normalize:
        tail=re.search(r'(?:\r?\n)+\Z',original)
        suffix=tail.group() if tail else ''
        core=original[:-len(suffix)] if suffix else original
        result=reorder_structured(core,operation,layout)
        source=re.split(r'\r?\n[ \t]*\r?\n',core)
        target=re.split(r'\r?\n[ \t]*\r?\n',result)
        if len(target)!=4 or Counter(target)!=Counter(source): raise ValueError('重排后未保留四段原文')
        return result+suffix
    structured_blocks(original)
    orders = {'conclusion_front': (1,0,2,3), 'conclusion_back': (0,2,3,1),
              'support_first': (0,1,3,2), 'limitation_first': (2,1,0,3)}
    if layout=='group_first_v2':
        orders.update(support_first=(0,3,1,2),limitation_first=(2,0,1,3))
    elif layout!='slot_first_v1': raise ValueError('未知结构位置布局版本')
    if operation not in orders: raise ValueError('未知结构位置操作')
    parts = re.split(r'(\r?\n[ \t]*\r?\n)', original)
    texts = parts[::2]; separators = parts[1::2]
    order = orders[operation]
    return ''.join(texts[index] + (separators[position] if position<3 else '')
                   for position,index in enumerate(order))


def result_matrix(store, graph):
    from .analysis import metrics
    from .protocol import item_settings
    series = {(s['context']['dataset_id'], s['context']['item_id'], s['context']['model_id'], s['context']['variant']): s
              for s in graph.series if s['context']['batch']=='main' and s['context']['repeat']==0}
    materials = {(m['dataset_id'], m['item_id'], m['model_id'], m['operation']): m for m in graph.materials}
    gold = {(r['dataset_id'], r['item_id']): r['label'] for r in store.get('analysis_inputs', 'gold')['gold']}
    rows = []
    for item in store.get('inputs', 'items')['items']:
        settings = item_settings(store.config, getattr(graph, 'amendment', None), item['dataset_id'], item['item_id'])
        for model in store.config['debate_models']:
            base = (item['dataset_id'], item['item_id'], model)
            for variant in ('O', *store.config['operations']):
                arm = series.get((*base, variant))
                material = materials.get((*base, variant))
                for protocol in ('I', 'M'):
                    round_no = settings['independent_rounds'] if protocol=='I' else 3
                    snapshot = arm['states'][protocol].get(str(round_no)) if arm else None
                    status = 'complete' if snapshot else 'pending'
                    reason = '' if snapshot else '等待本题完整合格材料及终局回复'
                    if not snapshot and material and material['status']!='eligible':
                        status = material['status']; reason = material['reason']
                    m = metrics(snapshot, item['labels'])
                    actual = gold.get(base[:2])
                    rows.append({'dataset_id': base[0], 'item_id': base[1], 'model_id': model,
                                 'matrix_layout':store.config.get('matrix_layout','slot_first_v1'),
                                 'design': settings['design'], 'variant': variant, 'repeat': 0,
                                 'protocol': protocol, 'round': round_no, 'status': status, 'reason': reason,
                                 'snapshot_id': snapshot['snapshot_id'] if snapshot else None,
                                 'task_ids': [r['task_id'] for r in snapshot['replies']] if snapshot else [],
                                 'parse_ids': [r['parse_id'] for r in snapshot['replies']] if snapshot else [],
                                 'votes': [r['value']['label'] for r in snapshot['replies']] if snapshot else [],
                                 'verdict': m['verdict'], 'gold': actual,
                                 'correct': m['verdict']==actual if snapshot and actual is not None else None})
    return rows


def matrix_summary(rows,store=None):
    from .protocol import STUDY_V2
    if store and store.config.get('study_protocol')==STUDY_V2:
        items=store.get('inputs','items')['items']
        expected={(i['dataset_id'],i['item_id'],m,v,p) for i in items for m in store.config['debate_models'] for v in ('O',*OPERATIONS) for p in ('I','M')}
        grouped=defaultdict(list)
        for row in rows: grouped[tuple(row[k] for k in ('dataset_id','item_id','model_id','variant','protocol'))].append(row)
        complete={key for key,values in grouped.items() if key in expected and len(values)==1 and values[0]['status']=='complete' and values[0]['round']==3 and len(values[0]['votes'])==3}
        by_item={(i['dataset_id'],i['item_id']) for i in items}
        return {'view':'v2 frozen inputs × models × canonical 8 materials × I/M-T3','expected_outputs':len(expected),
                'complete_outputs':len(complete),'total_items':len(by_item),
                'complete_items':sum(all(k in complete for k in expected if k[:2]==item) for item in by_item),
                'missing_rows':len(expected-set(grouped)),'duplicate_rows':sum(max(0,len(v)-1) for v in grouped.values()),
                'unexpected_rows':sum(len(v) for k,v in grouped.items() if k not in expected),
                'statuses':dict(Counter(r['status'] for r in rows))}
    by_item = defaultdict(list)
    for row in rows: by_item[(row['dataset_id'], row['item_id'])].append(row)
    return {'view': 'repeat=0; actual I final round and M-T3', 'expected_outputs': len(rows),
            'complete_outputs': sum(r['status']=='complete' for r in rows),
            'total_items': len(by_item),
            'complete_items': sum(all(r['status']=='complete' for r in group) for group in by_item.values()),
            'statuses': dict(Counter(r['status'] for r in rows))}
