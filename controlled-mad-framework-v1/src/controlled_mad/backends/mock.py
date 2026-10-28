"""可配置离线响应。仅用于工程验收，不把模拟输出当研究数据。"""
import json
from ..domain import ModelResult
from ..identity import canonical_bytes
class MockBackend:
    def __init__(self,scenario,on_raw=None): self.scenario=scenario; self.on_raw=on_raw
    def generate(self,task,profile):
        ctx=task.context; item=self.scenario['items'][ctx['item_id']]
        for override in self.scenario.get('overrides',[]):
            match=override['match']
            if all((task.stage if k=='stage' else ctx.get(k))==v for k,v in match.items()):
                text=override['text']; break
        else:
            index=int(ctx.get('agent','agent_1').split('_')[-1])-1
            if task.stage=='S0': value={'label':item['s0'][index],'comment':item['comment']}
            elif task.stage=='generate':
                if ctx['operation']=='neutral':
                    value={'label':ctx['label'],'comment':ctx['original'].replace('Support A: ','Support A: Restated, ',1)}
                else:
                    prefix={'ethos':'A careful assessment: ','logos':'The reasoning is: ','pathos':'Please consider: '}[ctx['operation']]
                    value={'label':ctx['label'],'comment':prefix+ctx['original']}
            elif task.stage=='annotate': value={'blocks':item['blocks']}
            elif task.stage=='audit': value={'verdict':item.get('audits',{}).get(ctx['operation'],'pass'),'reason':'Fixture-defined audit result; synthetic only.'}
            else:
                key=f"{ctx['batch']}/{ctx['variant']}/{ctx['protocol']}/{ctx['round']}"
                fallback=f"{ctx['variant']}/{ctx['protocol']}/{ctx['round']}"
                labels=item.get('rounds',{}).get(key,item.get('rounds',{}).get(fallback,item['s0']))
                value={'label':labels[index],'comment':f"Fixture reply from {ctx['agent']} at T{ctx['round']}."}
            text=json.dumps(value,ensure_ascii=False)
        raw=canonical_bytes({'model':profile.model,'text':text,'stop_reason':'stop','usage':{'input_tokens':len(canonical_bytes(task.messages)),'output_tokens':len(text.encode())},'synthetic':True})
        if self.on_raw: self.on_raw(raw,200,{})
        return self.decode(raw,profile)
    def decode(self,raw,profile):
        try:
            d=json.loads(raw)
            return ModelResult(raw,d['text'],d.get('model'),d.get('stop_reason'),d.get('usage'))
        except (ValueError,KeyError,TypeError):
            return ModelResult(raw,'',None,None,None,'mock 原始响应无效')
