import json, hashlib
from pathlib import Path
from .jsonl import from_rows
LABELS=('Supported','Refuted','Not Enough Evidence','Conflicting Evidence/Cherrypicking')

def convert(records,source_hash=''):
    rows=[]
    for index,record in enumerate(records):
        evidence=[]
        for qa in record.get('questions',[]):
            for answer in qa.get('answers',[]):
                if answer.get('answer'):
                    evidence.append(f"Q: {qa.get('question','')}\nA: {answer['answer']}\nSource: {answer.get('source_url','')}")
        claim=record.get('claim','')
        task_input=f'Claim:\n{claim}\n\nEvidence:\n'+'\n'.join(f'- {entry}' for entry in evidence)
        rows.append({'dataset_id':'averitec','item_id':f'averitec_{index:04d}','claim':claim,'evidence':evidence,'task_input':task_input,'labels':LABELS,'gold':record.get('label')})
    return from_rows(rows,source_hash)

class AveritecAdapter:
    def load(self,source):
        data=Path(source).read_bytes()
        return convert(json.loads(data),hashlib.sha256(data).hexdigest())
