"""阶段 DAG、轮次屏障、共享 T1 与每接收者状态分叉。"""
import json
from dataclasses import asdict
from .domain import AGENTS, Item, Task, Vote, Message, HistoryState
from .identity import canonical_hash, plain
from .config import profile
from .s0 import freeze_s0
from .history import extend_history, with_reply
from .router import route
from .interventions import resolve_rule

class Planner:
    def __init__(self,store,persist=True):
        self.store=store; self.config=store.config
        self.persist=persist
        self.prompts=store.get('inputs','prompts')
        self.selected=store.selection(); self.parse_cache={}; self.tasks={}
        self.materials=[]; self.comparisons=[]; self.series=[]; self.blocked=[]
    def put(self,collection,key,value):
        if self.persist: self.store.put(collection,key,value)
    def remember_message(self,message):
        self.put('messages',message.message_id,{**plain(message),'body_hash':canonical_hash([message.label,message.comment])})
    def parsed(self,task_id):
        pid=self.selected.get(task_id)
        if not pid: return None
        if pid not in self.parse_cache: self.parse_cache[pid]=self.store.get('parses',pid)
        return self.parse_cache[pid]
    def result(self,task_id):
        result=self.parsed(task_id)
        return result if result and result['valid'] else None
    def make(self,stage,profile_id,messages,dependencies,context):
        ctx=dict(context)
        ctx['sampling_identity']=canonical_hash([stage,context,list(dependencies)])
        payload={'stage':stage,'profile':plain(profile(self.config,profile_id)),'messages':list(messages),'dependencies':list(dependencies),'context':ctx}
        tid=canonical_hash(payload)
        task=Task(tid,stage,profile_id,tuple(messages),tuple(dependencies),ctx,canonical_hash(messages))
        self.put('tasks',tid,plain(task)); self.tasks[tid]=task
        if stage in ('T1','T2','T3'):
            self.put('deliveries',tid,{'task_id':tid,'prompt_hash':task.prompt_hash,**{k:ctx[k] for k in ('agent','round','protocol','branch','variant','delivered','blocked','history_seen')}})
        return task
    def prompt(self,name,payload):
        return ({'role':'system','content':self.prompts[name]},{'role':'user','content':json.dumps(payload,ensure_ascii=False)})
    def snapshot(self,context,round_no,tasks,histories,parent=None):
        responses=[self.result(t.task_id) for t in tasks]
        if not all(responses): return None
        updated={}; replies=[]
        for task,response in zip(tasks,responses):
            agent=task.context['agent']; pid=self.selected[task.task_id]
            updated[agent]=plain(with_reply(histories[agent],response['value'],pid))
            replies.append({'agent':agent,'task_id':task.task_id,'parse_id':pid,'value':response['value']})
            self.remember_message(Message(pid,agent,round_no,response['value']['label'],response['value']['comment'],pid,context['variant']))
        payload={'context':context,'round':round_no,'replies':replies,'histories':updated,'parent':parent}
        sid=canonical_hash(payload); payload['snapshot_id']=sid
        self.put('snapshots',sid,payload)
        return payload
    def round(self,base,round_no,protocol,previous,rule=None):
        if previous is None: return None
        peers=tuple(Message(r['parse_id'],r['agent'],round_no-1,r['value']['label'],r['value']['comment'],r['parse_id'],base['variant']) for r in previous['replies'])
        order=self.config['message_order']; peers=tuple(sorted(peers,key=lambda m:order.index(m.sender)))
        histories={}; tasks=[]
        for agent in AGENTS:
            old=previous['histories'][agent]; history=HistoryState(tuple(old['messages']),tuple(old['seen']))
            delivery=route(history,round_no,protocol,agent,rule,peers)
            histories[agent]=extend_history(history,delivery,'Reconsider now. Return JSON with label and comment using only the allowed labels.')
            ctx={**base,'agent':agent,'round':round_no,'protocol':protocol,'branch':rule['id'] if rule else 'natural',
                'parent_snapshot':previous['snapshot_id'],'delivered':[m.message_id for m in delivery.delivered],'blocked':list(delivery.blocked),'history_seen':list(histories[agent].seen)}
            tasks.append(self.make(f'T{round_no}',base['model_id'],histories[agent].messages,tuple(r['task_id'] for r in previous['replies']),ctx))
        return self.snapshot({**base,'protocol':protocol,'branch':rule['id'] if rule else 'natural'},round_no,tasks,histories,previous['snapshot_id'])
    def arm(self,item,s0,base,comment,variant,repeat,batch='main'):
        base={**base,'variant':variant,'repeat':repeat,'batch':batch}
        initial=[]
        for i,agent in enumerate(AGENTS):
            value={'label':s0.votes[i].label,'comment':comment if agent==s0.sender else s0.votes[i].comment}
            version=variant if agent==s0.sender else 'S0'
            message=Message(canonical_hash([s0.source_ids[i],value,version]),agent,0,value['label'],value['comment'],s0.source_ids[i],version)
            initial.append(message); self.remember_message(message)
        tasks=[]; histories={}
        for i,agent in enumerate(AGENTS):
            own=initial[i]
            messages=(*self.prompt('debate',{'task_input':item.task_input,'allowed_labels':list(item.labels)}),
                {'role':'assistant','content':json.dumps({'label':own.label,'comment':own.comment},ensure_ascii=False)})
            history=HistoryState(messages,(own.message_id,))
            delivery=route(history,1,'shared',agent,None,tuple(sorted(initial,key=lambda m:self.config['message_order'].index(m.sender))))
            histories[agent]=extend_history(history,delivery,'Read the initial peer comments and give your revised label and comment.')
            ctx={**base,'agent':agent,'round':1,'protocol':'shared','branch':'natural','delivered':[m.message_id for m in delivery.delivered],'blocked':[],
                'history_seen':list(histories[agent].seen)}
            tasks.append(self.make('T1',base['model_id'],histories[agent].messages,tuple(v.task_id for v in s0.votes),ctx))
        first=self.snapshot({**base,'protocol':'shared','branch':'natural'},1,tasks,histories,s0.s0_hash)
        result={'I':{'1':first},'M':{'1':first},'interventions':{}}
        for protocol in ('I','M'):
            second=self.round(base,2,protocol,first)
            third=self.round(base,3,protocol,second)
            result[protocol].update({'2':second,'3':third})
        if batch=='main':
            for intervention in self.config['interventions']:
                rule=resolve_rule(intervention,s0.sender)
                if rule['round']==2:
                    second=self.round(base,2,'M',first,rule)
                    third=self.round(base,3,'M',second,rule)
                else:
                    second=result['M']['2']
                    third=self.round(base,3,'M',second,rule)
                result['interventions'][rule['id']]={'1':first,'2':second,'3':third}
        self.series.append({'context':base,'states':result})
        return result
    def refresh_item(self,item_key):
        """仅重建发生变化的题目；题间无依赖，保留其他题目的完整视图。"""
        matches=lambda ctx:(ctx['dataset_id'],ctx['item_id'])==item_key
        updated=Planner(self.store,persist=self.persist).build(item_key=item_key)
        self.selected=updated.selected
        self.parse_cache={}
        self.tasks={tid:task for tid,task in self.tasks.items() if not matches(task.context)}
        self.tasks.update(updated.tasks)
        for name in ('materials','comparisons','series','blocked'):
            old=getattr(self,name)
            retained=[row for row in old if not matches(row.get('context',row))]
            setattr(self,name,retained+getattr(updated,name))
        return self

    def build(self,item_key=None):
        from .materials import prepare_materials
        for raw in self.store.get('inputs','items')['items']:
            if item_key is not None and (raw['dataset_id'],raw['item_id'])!=item_key: continue
            item=Item(raw['dataset_id'],raw['item_id'],raw['claim'],tuple(raw['evidence']),tuple(raw['labels']),raw.get('task_input',''),tuple(tuple(pair) for pair in raw.get('metadata',())))
            for model in self.config['debate_models']:
                base={'dataset_id':item.dataset_id,'item_id':item.item_id,'model_id':model,'s0_batch':self.config['s0_batch'],'labels':list(item.labels)}
                tasks=[]
                for agent in AGENTS:
                    tasks.append(self.make('S0',model,self.prompt('s0',{'task_input':item.task_input,'allowed_labels':list(item.labels)}),(),{**base,'agent':agent,'round':0}))
                responses=[self.result(t.task_id) for t in tasks]
                if not all(responses):
                    self.blocked.append({**base,'stage':'materials','reason':'S0 三份有效回复尚未齐备'})
                    continue
                votes=tuple(Vote(r['value']['label'],r['value']['comment'],t.task_id,r['attempt_id'],self.selected[t.task_id]) for t,r in zip(tasks,responses))
                s0=freeze_s0(votes,{'dataset_id':item.dataset_id,'item_id':item.item_id,'seed':self.config['selection_seed']})
                frozen={**base,'sender':s0.sender,'s0_hash':s0.s0_hash,'source_ids':list(s0.source_ids),'initial_labels':[v.label for v in votes]}
                self.put('snapshots',s0.s0_hash,{'kind':'frozen_s0',**frozen,'votes':plain(votes)})
                materials=prepare_materials(self,item,s0,frozen)
                self.materials.extend(materials)
                if any(m['status']=='pending' for m in materials):
                    self.blocked.append({**base,'stage':'T1','reason':'材料生成/审核尚未结束'})
                    continue
                accepted=[m for m in materials if m['status']=='eligible']
                original=votes[AGENTS.index(s0.sender)].comment
                for repeat in range(self.config['repeats']):
                    if not accepted: break
                    o=self.arm(item,s0,frozen,original,'O',repeat)
                    for material in accepted:
                        r=self.arm(item,s0,frozen,material['rewritten'],material['operation'],repeat)
                        context={**frozen,'operation':material['operation'],'repeat':repeat,'material_id':material['material_id']}
                        comparison={'context':context,'O':o,'R':r}
                        cid=canonical_hash([context,material['material_id']]); comparison['comparison_id']=cid
                        self.comparisons.append(comparison)
                        refs=lambda arm:{p:{t:s['snapshot_id'] if s else None for t,s in states.items()} for p,states in arm.items() if p in ('I','M')}
                        # 持久化不可变快照引用；完成状态变化产生新对象，不覆盖旧版本。
                        view={'comparison_id':cid,'context':context,'O':refs(o),'R':refs(r)}
                        self.put('comparisons',canonical_hash(view),view)
                oo=self.config['diagnostics']['oo']
                if oo['enabled']:
                    for repeat in range(oo['repeats']):
                        for batch in ('oo_1','oo_2'): self.arm(item,s0,frozen,original,'O',repeat,batch)
        return self

def advance(run):
    return tuple(Planner(run).build().tasks.values())
