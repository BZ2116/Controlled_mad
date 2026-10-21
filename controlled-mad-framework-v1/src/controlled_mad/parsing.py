import json

def json_object(text, allow_fence=True):
    text=text.strip()
    if allow_fence and text.startswith('```') and text.endswith('```'):
        lines=text.splitlines()
        if lines[0] not in ('```','```json'): raise ValueError('未知代码块类型')
        text='\n'.join(lines[1:-1])
    value=json.loads(text)
    if not isinstance(value,dict): raise ValueError('回复必须为一个 JSON 对象')
    return value

def parse_vote(text,labels,parser_version='vote-v1'):
    try:
        if parser_version not in ('vote-v1','vote-v2'): raise ValueError('未登记解析器版本')
        obj=json_object(text,allow_fence=parser_version=='vote-v2')
        if set(obj)-{'label','comment','confidence'}: raise ValueError('回复含未知字段')
        original=obj['label']; comment=obj['comment']
        if not isinstance(original,str) or not isinstance(comment,str) or not comment.strip(): raise ValueError('缺少有效标签或评论')
        mapping={s.casefold():s for s in labels}
        label=mapping.get(original.strip().casefold())
        if label is None: raise ValueError('标签越界；不做语义推测')
        return {'valid':True,'value':{'label':label,'comment':comment},'original_label':original,'normalization':original!=label,'error':None}
    except (ValueError,KeyError,TypeError) as e:
        return {'valid':False,'value':None,'error':str(e)}

def parse_audit(text):
    obj=json_object(text)
    if obj.get('verdict') not in ('pass','fail','uncertain') or not isinstance(obj.get('reason'),str) or not obj['reason'].strip(): raise ValueError('审核必须返回 verdict 和非空 reason')
    return {'verdict':obj['verdict'],'reason':obj['reason']}
