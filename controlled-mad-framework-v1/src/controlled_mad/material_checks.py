import re
from decimal import Decimal, DecimalException
from urllib.parse import urlsplit

def check_material(original,rewritten,label,returned_label,version='legacy_v1'):
    from .protocol import STUDY_V2
    if version==STUDY_V2: return _normalized_check(original,rewritten,label,returned_label)
    valid=bool(rewritten.strip()) and label==returned_label
    def extract(pattern,text): return sorted(set(re.findall(pattern,text)))
    return {'valid':valid,'no_change':original==rewritten,
        'new_numbers':sorted(set(extract(r'\d+(?:\.\d+)?',rewritten))-set(extract(r'\d+(?:\.\d+)?',original))),
        'new_urls':sorted(set(extract(r'https?://[^\s]+',rewritten))-set(extract(r'https?://[^\s]+',original))),
        'length_ratio':len(rewritten)/max(len(original),1)}


def _normalized_check(original,rewritten,label,returned_label):
    number=re.compile(r'(?<![\w.])[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?(?:\s*(?:%|percent\b|per cent\b))?',re.I)
    domain=re.compile(r'''https?://[^\s<>"']+|(?<![\w@])(?:www\.)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}(?::\d+)?(?:/[^\s<>"']*)?''',re.I)
    def numbers(text):
        result=set()
        for match in number.finditer(text):
            value=match.group(); percent=bool(re.search(r'%|percent|per cent',value,re.I))
            value=re.sub(r'\s*(?:%|percent|per cent)\s*$','',value,flags=re.I)
            raw=value.replace(',','')
            try:
                sign,digits,exponent=Decimal(raw).as_tuple()
                digits=list(digits)
                if not any(digits): normalized='0'
                else:
                    while digits[-1]==0: digits.pop(); exponent+=1
                    # Tuple reconstruction is exact: no context rounding and no
                    # fixed-format expansion of adversarially large exponents.
                    normalized=str(Decimal((sign,tuple(digits),exponent)))
            except (DecimalException,ValueError,OverflowError): normalized=raw.lower()
            result.add(normalized+('%' if percent else ''))
        return result
    def urls(text):
        result=set()
        for match in domain.finditer(text):
            value=match.group().rstrip('.,;:!?')
            while value and value[-1] in ')]}' and value.count(value[-1])>value.count({')':'(',']':'[','}':'{'}[value[-1]]): value=value[:-1]
            u=urlsplit(value if '://' in value else 'https://'+value)
            host=(u.hostname or '').lower()
            try: port=u.port
            except ValueError: port=None
            if port in (80,443): port=None
            result.add(host+(':'+str(port) if port else '')+(u.path or '/')+('?' + u.query if u.query else '')+('#'+u.fragment if u.fragment else ''))
        return result
    a,b=numbers(original),numbers(rewritten); ua,ub=urls(original),urls(rewritten)
    return {'valid':bool(rewritten.strip()) and label==returned_label,'no_change':original==rewritten,
            'new_numbers':sorted(b-a),'new_urls':sorted(ub-ua),'length_ratio':len(rewritten)/max(len(original),1),
            'check_version':'normalized_v2','original_numbers':sorted(a),'rewritten_numbers':sorted(b),
            'original_urls':sorted(ua),'rewritten_urls':sorted(ub)}

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
