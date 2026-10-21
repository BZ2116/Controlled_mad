"""本地 MMLU-Pro JSON/JSONL 适配；答案仅进入隔离金标。"""
from pathlib import Path
from .base import read_records, source_digest
from .jsonl import from_rows

LABELS=tuple('ABCDEFGHIJ')

def _gold(record,labels):
    answer=record.get('answer')
    if isinstance(answer,str) and answer.strip().upper() in labels:
        return answer.strip().upper()
    index=record.get('answer_index')
    if type(index) is int and 0<=index<len(labels):
        return labels[index]
    raise ValueError('缺少有效 answer 标签或 answer_index')

def convert(records,source_hash=''):
    rows=[]
    for index,record in enumerate(records):
        try:
            question=record.get('question')
            options=record.get('options')
            if not isinstance(question,str) or not question.strip(): raise ValueError('question 不能为空')
            if not isinstance(options,list) or not 2<=len(options)<=len(LABELS) or any(not isinstance(x,str) or not x.strip() for x in options):
                raise ValueError('options 必须含 2 到 10 个非空字符串')
            labels=LABELS[:len(options)]
            gold=_gold(record,labels)
            choices=[f'{label}. {option}' for label,option in zip(labels,options)]
            task_input=f'Question:\n{question.strip()}\n\nAnswer choices:\n'+'\n'.join(choices)
            category=record.get('category')
            metadata={'category':category} if isinstance(category,str) and category else {}
            source=record.get('src')
            if isinstance(source,str) and source: metadata['source']=source
            rows.append({'dataset_id':'mmlu_pro','item_id':f'mmlu_pro_{index:05d}','claim':question.strip(),
                'evidence':[f'Option {line}' for line in choices],'task_input':task_input,'labels':labels,'gold':gold,'metadata':metadata})
        except (TypeError,ValueError,AttributeError) as error:
            rows.append({'dataset_id':'mmlu_pro','item_id':f'mmlu_pro_{index:05d}','claim':'','evidence':[],'labels':[],'_reject_reason':str(error)})
    bundle=from_rows([row for row in rows if '_reject_reason' not in row],source_hash)
    rejected=list(bundle.rejected)
    rejected.extend({'dataset_id':row['dataset_id'],'item_id':row['item_id'],'index':index,'reason':row['_reject_reason']} for index,row in enumerate(rows) if '_reject_reason' in row)
    return type(bundle)(bundle.items,bundle.gold,bundle.source_hash,tuple(sorted(rejected,key=lambda x:x['index'])))

class MMLUProAdapter:
    def load(self,source:Path):
        raw,records=read_records(source)
        return convert(records,source_digest(raw))
