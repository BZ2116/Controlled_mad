import re

def check_material(original,rewritten,label,returned_label):
    valid=bool(rewritten.strip()) and label==returned_label
    def extract(pattern,text): return sorted(set(re.findall(pattern,text)))
    return {'valid':valid,'no_change':original==rewritten,
        'new_numbers':sorted(set(extract(r'\d+(?:\.\d+)?',rewritten))-set(extract(r'\d+(?:\.\d+)?',original))),
        'new_urls':sorted(set(extract(r'https?://[^\s]+',rewritten))-set(extract(r'https?://[^\s]+',original))),
        'length_ratio':len(rewritten)/max(len(original),1)}

def validate_blocks(blocks):
    if not isinstance(blocks,list) or any(not isinstance(b,dict) or set(b)!={'text','kind'} or not isinstance(b['text'],str) or not b['text'] or not isinstance(b['kind'],str) or b['kind'] not in ('support','limitation','conclusion','other') for b in blocks):
        raise ValueError('原文块结构无效')

def reorder(original,blocks,operation):
    validate_blocks(blocks)
    if not blocks: raise ValueError('原文块为空')
    if ''.join(b['text'] for b in blocks)!=original: raise ValueError('原文块未精确覆盖原文')
    if any(original.count(b['text'])!=1 for b in blocks): raise ValueError('重复子串无法唯一定位')
    if operation.startswith('conclusion_'):
        conclusions=[b for b in blocks if b['kind']=='conclusion']
        if len(conclusions)!=1: raise ValueError('需要唯一独立结论块')
        rest=[b for b in blocks if b['kind']!='conclusion']
        result=conclusions+rest if operation=='conclusion_front' else rest+conclusions
    else:
        selected=[b for b in blocks if b['kind'] in ('support','limitation')]
        if {b['kind'] for b in selected}!={'support','limitation'}: raise ValueError('原文缺少支持点或限制点')
        first='support' if operation=='support_first' else 'limitation'
        ordered=iter(sorted(selected,key=lambda b:b['kind']!=first))
        result=[next(ordered) if b['kind'] in ('support','limitation') else b for b in blocks]
    return ''.join(b['text'] for b in result)
