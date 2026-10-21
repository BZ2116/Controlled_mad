"""命令行与外部驱动共用的公共入口。"""
from pathlib import Path
from collections import Counter
from .engine import create_run, execute_ready, submit, status, new_attempt, reparse, fork_revision
from .store import RunStore
from .config import load_config
from .scheduler import Planner
from .identity import canonical_hash, canonical_bytes, plain, safe_id
from .analysis import analyze
from .analysis_rq2 import analyze_rq2
from .subset import select_bundle
from .reporting import export_analysis, trace
from .datasets.base import adapter_for
from .engine import task_state, ROOT
from .recovery import verify_run


def open_run(run_id,root=None):
    return RunStore((Path(root) if root else Path.cwd()/'runs')/safe_id(run_id))

def validate(path):
    c=load_config(path)
    adapter=adapter_for(c['dataset']['adapter'])
    b=adapter.load(c['dataset']['source'])
    selection=None
    if c.get('experiment')=='rq2_five_round': b,selection=select_bundle(b,c['subset'])
    strata={}
    for item in b.items:
        for key,value in item.metadata:
            strata.setdefault(key,Counter())[value]+=1
    return {'config_hash':canonical_hash(c),'dataset_id':c['dataset']['adapter'],'items':len(b.items),'rejected':len(b.rejected),
        'label_spaces':[list(labels) for labels in sorted({i.labels for i in b.items})],'strata':{k:dict(v) for k,v in sorted(strata.items())},
        'profiles':list(c['profiles']),'network_enabled':c['network_enabled'],**({'subset':selection} if selection else {})}

def plan(path):
    info=validate(path); c=load_config(path)
    n=info['items']*len(c['debate_models']); k=len(c['operations']); r=c['repeats']
    rhetoric=sum(op in ('ethos','logos','pathos') for op in c['operations']); reorder=k-rhetoric
    material_generate=n*rhetoric*c['max_generation_attempts']
    annotation=n if reorder else 0
    audit=n*(rhetoric*c['max_generation_attempts']+reorder) if c.get('material_audit',True) else 0
    natural=15*n*r*(1+k)
    interventions=sum((6 if rule['delivery_round']==2 else 3)*n*r*(1+k) for rule in c['interventions'])
    oo=30*n*c['diagnostics']['oo']['repeats'] if c['diagnostics']['oo']['enabled'] else 0
    physical={'S0':3*n,'generate':material_generate,'annotate':annotation,'audit':audit,'natural_replies':natural,'intervention_replies':interventions,'oo_replies':oo}
    if c.get('experiment')=='rq2_five_round':
        physical={'S0':3*n,'generate':material_generate,'annotate':annotation,'audit':audit,
                  **{f'T{round_no}':3*n*r*(1+k) for round_no in range(1,6)}}
        return {**info,'experiment':'rq2_five_round','logical_comparison_repeats_upper':n*k*r,
            'physical_tasks_upper':physical,'total_tasks_upper':sum(physical.values()),'hard_budget':c['budget'],
            'note':'资格冻结前的五轮 M-O/M-R 任务上界；T5 为终局，无 I 臂。'}
    return {**info,'logical_comparison_repeats_upper':n*k*r,'logical_four_cells_upper':4*n*k*r,'physical_tasks_upper':physical,'total_tasks_upper':sum(physical.values()),
        'hard_budget':c['budget'],'note':'资格冻结前的任务上界；不含显式失败重答，实际数量依材料合格与复用关系而变。输入 token 逐任务检查，不伪造预先精确估算。'}

def advance(store):
    with store.lock():
        graph=Planner(store).build()
        return tuple(graph.tasks.values())

def export_tasks(store,stage=None):
    with store.lock():
        graph=Planner(store).build(); events=store.all('events'); selected=store.selection()
        rows=[]
        for t in graph.tasks.values():
            if (stage is None or t.stage==stage) and task_state(store,t.task_id,events,selected)=='ready':
                rows.append({**plain(t),'attempt_id':f'{t.task_id}-a{len(store.attempts(t.task_id))+1:03d}'})
        name='tasks-'+canonical_hash(rows)[:20]
        store.write_blob('exports',name,b''.join(canonical_bytes(row)+b'\n' for row in rows),'.jsonl')
        return {'tasks':len(rows),'path':str(store.path/'exports'/f'{name}.jsonl'),'instruction':'仅 messages 为模型可见请求，context 为执行元数据。'}

def analyze_and_export(store):
    with store.lock():
        verify_run(store)
        bundle=analyze_rq2(store) if store.config.get('experiment')=='rq2_five_round' else analyze(store)
        return {**export_analysis(bundle,store),'denominators':bundle['denominators']}

def export_trace(store,item_id):
    with store.lock(): return trace(store,item_id)
