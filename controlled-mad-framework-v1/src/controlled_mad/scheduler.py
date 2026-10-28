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
from .pipeline import item_pipeline_enabled, freeze_item_materials
from .protocol import load_amendment, item_settings, SINGLE_PASS, EQUAL_ROUNDS

def complete_arm(arm): return all(snapshot for states in arm.values() for snapshot in states.values())

class Planner:
    def __init__(self,store,persist=True):
        self.store=store; self.config=store.config
        self.persist=persist
        self.amendment=load_amendment(store)
        self.prompts=store.get('inputs','prompts')
        self.selected=store.selection(); self.parse_cache={}; self.tasks={}
        self.materials=[]; self.comparisons=[]; self.series=[]; self.blocked=[]
        self.material_freeze=(store.get('inputs','material-freeze')
                              if self.config.get('material_gate') and (store.path/'inputs'/'material-freeze.json').exists() else None)
        self.item_pipeline=item_pipeline_enabled(store)
        self.intervention_items=(set(store.get('inputs','intervention-subset')['selected_item_ids'])
                                 if self.config.get('intervention_subset') else None)
    def settings(self,context):
        return item_settings(self.config,self.amendment,context['dataset_id'],context['item_id'])
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
        if stage in ('T1','T2','T3','T4','T5'):
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
            instruction=('This is round '+str(round_no)+' of 5. Your answer is provisional until round 5. Reconsider now. Return JSON with label and comment using only the allowed labels.'
                         if self.config.get('experiment')=='rq2_five_round' else 'Reconsider now. Return JSON with label and comment using only the allowed labels.')
            histories[agent]=extend_history(history,delivery,instruction)
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
            instruction=('This is round 1 of 5. Read the initial peer comments and give your provisional revised label and comment.'
                         if self.config.get('experiment')=='rq2_five_round' else 'Read the initial peer comments and give your revised label and comment.')
            histories[agent]=extend_history(history,delivery,instruction)
            ctx={**base,'agent':agent,'round':1,'protocol':'shared','branch':'natural','delivered':[m.message_id for m in delivery.delivered],'blocked':[],
                'history_seen':list(histories[agent].seen)}
            tasks.append(self.make('T1',base['model_id'],histories[agent].messages,tuple(v.task_id for v in s0.votes),ctx))
        first=self.snapshot({**base,'protocol':'shared','branch':'natural'},1,tasks,histories,s0.s0_hash)
        if self.config.get('experiment')=='rq2_five_round':
            result={'M':{'1':first}}
            previous=first
            for round_no in range(2,6):
                previous=self.round(base,round_no,'M',previous)
                result['M'][str(round_no)]=previous
            self.series.append({'context':base,'states':result})
            return result
        result={'I':{'1':first},'M':{'1':first},'interventions':{}}
        for protocol in ('I','M'):
            if protocol=='I' and self.settings(base)['independent_rounds']==1: continue
            second=self.round(base,2,protocol,first)
            third=self.round(base,3,protocol,second)
            result[protocol].update({'2':second,'3':third})
        if batch=='main' and (self.intervention_items is None or item.item_id in self.intervention_items):
            for intervention in self.config['interventions']:
                rule=resolve_rule(intervention,s0.sender)
                if rule['round']==2:
                    second=self.round(base,2,'M',first,rule)
                    third=self.round(base,3,'M',second,rule)
                else:
                    second=result['M']['2']
                    third=self.round(base,3,'M',second,rule)
                result['interventions'][rule['id']]={'1':first,'2':second,'3':third}
        design=self.settings(base)['design']
        series_context=({**base,'design':design} if design in (SINGLE_PASS,EQUAL_ROUNDS) else base)
        self.series.append({'context':series_context,'states':result})
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

    def build(self,item_key=None,progress=None):
        from .materials import prepare_materials
        rows=self.store.get('inputs','items')['items']
        for index,raw in enumerate(rows,1):
            if item_key is not None and (raw['dataset_id'],raw['item_id'])!=item_key: continue
            if progress: progress({'item_id':raw['item_id'],'item_position':index,'item_total':len(rows)})
            item=Item(raw['dataset_id'],raw['item_id'],raw['claim'],tuple(raw['evidence']),tuple(raw['labels']),raw.get('task_input',''),tuple(tuple(pair) for pair in raw.get('metadata',())))
            for model in self.config['debate_models']:
                base={'dataset_id':item.dataset_id,'item_id':item.item_id,'model_id':model,'s0_batch':self.config['s0_batch'],'labels':list(item.labels)}
                tasks=[]
                for agent in AGENTS:
                    ctx={**base,'agent':agent,'round':0}
                    if self.config.get('complete_matrix'):
                        from .matrix import STRUCTURE
                        ctx['comment_structure']=STRUCTURE
                    tasks.append(self.make('S0',model,self.prompt('s0',{'task_input':item.task_input,'allowed_labels':list(item.labels)}),(),ctx))
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
                if self.config.get('complete_matrix') and any(m['status']!='eligible' for m in materials):
                    self.blocked.append({**base,'stage':'T1','reason':'完整矩阵要求7种变体全部合格且互异，缺失不能跳过'})
                    continue
                if self.item_pipeline and not any(m['status']=='pending' for m in materials):
                    freeze_item_materials(self.store,frozen,materials,self.persist)
                if self.config.get('material_gate'):
                    if self.material_freeze is None and not self.item_pipeline:
                        self.blocked.append({**base,'stage':'T1','reason':'全体材料结束后须 freeze-materials 冻结资格清单'})
                        continue
                    if self.material_freeze is not None and any(m['material_id'] not in self.material_freeze['material_ids'] for m in materials):
                        raise ValueError('材料或 S0 已偏离冻结资格清单，请建立新运行，不能在原运行重新入组')
                if any(m['status']=='pending' for m in materials):
                    self.blocked.append({**base,'stage':'T1','reason':'材料生成/审核尚未结束'})
                    continue
                accepted=[m for m in materials if m['status']=='eligible']
                original=votes[AGENTS.index(s0.sender)].comment
                for repeat in range(self.settings(base)['repeats']):
                    if not accepted: break
                    o=self.arm(item,s0,frozen,original,'O',repeat)
                    for material in accepted:
                        r=self.arm(item,s0,frozen,material['rewritten'],material['operation'],repeat)
                        context={**frozen,'operation':material['operation'],'repeat':repeat,'material_id':material['material_id']}
                        if self.settings(base)['design'] in (SINGLE_PASS,EQUAL_ROUNDS): context['design']=self.settings(base)['design']
                        comparison={'context':context,'O':o,'R':r}
                        cid=canonical_hash([context,material['material_id']]); comparison['comparison_id']=cid
                        self.comparisons.append(comparison)
                        refs=lambda arm:{p:{t:s['snapshot_id'] if s else None for t,s in states.items()} for p,states in arm.items() if p in ('I','M')}
                        # 只落盘 O、R 都跑满的版本。build() 每次重建都会重跑这一段，
                        # 连半成品一起写，落盘集合就成了调度时序的函数：中断续跑会攒出一批
                        # 单次跑完永远不会有的中间版本，内容寻址因此失去可复现性。
                        view={'comparison_id':cid,'context':context,'O':refs(o),'R':refs(r)}
                        if complete_arm(view['O']) and complete_arm(view['R']):
                            self.put('comparisons',canonical_hash(view),view)
                oo=self.config['diagnostics']['oo']
                if oo['enabled']:
                    for repeat in range(oo['repeats']):
                        for batch in ('oo_1','oo_2'): self.arm(item,s0,frozen,original,'O',repeat,batch)
        return self

def advance(run):
    return tuple(Planner(run).build().tasks.values())

