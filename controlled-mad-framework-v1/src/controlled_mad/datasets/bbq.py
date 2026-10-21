"""本地 BBQ JSON/JSONL 适配；情境与偏差类别保留，金标隔离。"""
from pathlib import Path
from .base import read_records, source_digest
from .jsonl import from_rows

LABELS=('A','B','C')

def _choices(record):
    answers=record.get('answers')
    if isinstance(answers,list): return answers
    if all(f'ans{i}' in record for i in range(3)): return [record[f'ans{i}'] for i in range(3)]
    if all(f'answer_{i}' in record for i in range(3)): return [record[f'answer_{i}'] for i in range(3)]
    raise ValueError('答案选项必须是长度为 3 的 answers 或 ans0/ans1/ans2')

def _gold(record,choices):
    value=record.get('answer_label',record.get('label'))
    if type(value) is int and 0<=value<3: return LABELS[value]
    if isinstance(value,str):
        normalized=value.strip().upper()
        if normalized in LABELS: return normalized
        if normalized.isdigit() and 0<=int(normalized)<3: return LABELS[int(normalized)]
        matches=[i for i,choice in enumerate(choices) if choice.strip()==value.strip()]
        if len(matches)==1: return LABELS[matches[0]]
    raise ValueError('缺少有效 BBQ label/answer_label')

def convert(records,source_hash=''):
    rows=[]; explicit_rejections=[]
    for index,record in enumerate(records):
        item_id=f"bbq_{record.get('example_id',index)}"
        try:
            context=record.get('context')
            question=record.get('question')
            if not isinstance(context,str) or not context.strip(): raise ValueError('context 不能为空')
            if not isinstance(question,str) or not question.strip(): raise ValueError('question 不能为空')
            choices=_choices(record)
            if len(choices)!=3 or any(not isinstance(x,str) or not x.strip() for x in choices): raise ValueError('BBQ 必须提供三个非空答案选项')
            gold=_gold(record,choices)
            options='\n'.join(f'{label}. {choice.strip()}' for label,choice in zip(LABELS,choices))
            task_input=f'Context:\n{context.strip()}\n\nQuestion:\n{question.strip()}\n\nAnswer choices:\n{options}'
            evidence=[f'Context: {context.strip()}',*[f'Option {label}: {choice.strip()}' for label,choice in zip(LABELS,choices)]]
            metadata={key:str(record[key]) for key in ('category','context_condition','question_polarity','is_ambiguous','target_loc') if record.get(key) is not None}
            rows.append({'dataset_id':'bbq','item_id':item_id,'claim':question.strip(),'evidence':evidence,'task_input':task_input,'labels':LABELS,'gold':gold,'metadata':metadata})
        except (TypeError,ValueError,AttributeError) as error:
            explicit_rejections.append({'dataset_id':'bbq','item_id':item_id,'index':index,'reason':str(error)})
    bundle=from_rows(rows,source_hash)
    return type(bundle)(bundle.items,bundle.gold,bundle.source_hash,tuple(sorted((*bundle.rejected,*explicit_rejections),key=lambda x:x['index'])))

class BBQAdapter:
    def load(self,source:Path):
        raw,records=read_records(source)
        return convert(records,source_digest(raw))
