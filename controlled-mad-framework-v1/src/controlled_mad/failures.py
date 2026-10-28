"""追加式逐题失败策略：有限重答、有效修复选择、缺格留档与断点跳过。"""
import csv
import io
import json

from .identity import canonical_hash


def enabled(store, requested=False):
    return requested or next((e.get('continue_on_error',False) for e in reversed(store.all('events'))
                              if e['kind']=='execution_policy'),False)


def skipped_items(store):
    skipped={}
    for e in store.all('events'):
        if e['kind']=='item_skipped':
            skipped[(e['dataset_id'],e['item_id'])]=e
        elif e['kind']=='item_reopened':
            skipped.pop((e['dataset_id'],e['item_id']),None)
    return skipped


def global_backend_error(error):
    return bool(error and (error.startswith(('HTTP 401','HTTP 402','HTTP 403')) or '模型标识' in error))


def exhausted_global_failure(store,graph,states,allowed,preflight_failures):
    from .engine import chargeable_attempts
    retired=retired_material_tasks(graph); skipped=skipped_items(store)
    for tid,state in states.items():
        t=graph.tasks[tid]; key=(t.context['dataset_id'],t.context['item_id'])
        if state!='invalid' or tid in retired or key in skipped or (allowed is not None and key not in allowed): continue
        p=graph.parsed(tid)
        if p and global_backend_error(p.get('backend_error')) and chargeable_attempts(store,tid,preflight_failures)>=store.config['max_attempts']:
            return tid
    return None


def adopt_valid_repairs(store, graph, states):
    retired=retired_material_tasks(graph); skipped=skipped_items(store)
    bad={tid for tid,t in graph.tasks.items() if states[tid]=='invalid' and tid not in retired
         and (t.context['dataset_id'],t.context['item_id']) not in skipped
         and graph.selected.get(tid)}
    changed=set()
    for e in reversed(store.all('events')):
        tid=e.get('task_id')
        if e['kind']!='finished' or tid not in bad: continue
        p=store.get('parses',e['parse_id'])
        if not p['valid']: continue
        store.event('selected',task_id=tid,parse_id=e['parse_id'],attempt_id=e['attempt_id'],
                    reason='auto_valid_repair: replace invalid selection only')
        bad.remove(tid)
        ctx=graph.tasks[tid].context; changed.add((ctx['dataset_id'],ctx['item_id']))
    return changed


def retired_material_tasks(graph):
    maximum={}
    eligible={(m['dataset_id'],m['item_id'],m['model_id'],m['operation']) for m in graph.materials if m['status']=='eligible'}
    material_tasks=[]
    for tid,t in graph.tasks.items():
        if t.stage not in ('generate','audit'): continue
        c=t.context; key=(c['dataset_id'],c['item_id'],c['model_id'],c['operation'])
        maximum[key]=max(maximum.get(key,0),c['generation']); material_tasks.append((tid,key,c['generation']))
    return {tid for tid,key,generation in material_tasks if key in eligible or generation<maximum[key]}


def queue_invalid_retries(store, graph, states, allowed, complete, preflight_failures):
    from .engine import chargeable_attempts
    retired=retired_material_tasks(graph); changed=set(); skipped=skipped_items(store)
    for tid,state in states.items():
        if state!='invalid' or tid in retired: continue
        c=graph.tasks[tid].context; key=(c['dataset_id'],c['item_id'])
        if complete[key] or key in skipped or (allowed is not None and key not in allowed): continue
        if chargeable_attempts(store,tid,preflight_failures)>=store.config['max_attempts']: continue
        starts=store.attempts(tid)
        store.event('retry_requested',task_id=tid,attempt_id=f'{tid}-a{len(starts)+1:03d}',
                    reason='auto_invalid_retry: output failed validation')
        changed.add(key)
    return changed


def skip_stalled_items(store, graph, states, allowed, complete, ready):
    from .matrix import result_matrix
    skipped=skipped_items(store)
    runnable={(t.context['dataset_id'],t.context['item_id']) for t in ready}
    stalled={key for key in complete if not complete[key] and key not in skipped and key not in runnable
             and (allowed is None or key in allowed)}
    if not stalled: return []
    rows=result_matrix(store,graph); recorded=[]
    for key in sorted(stalled):
        errors=[]
        for tid,t in graph.tasks.items():
            if (t.context['dataset_id'],t.context['item_id'])!=key or states[tid] not in ('invalid','transport_unknown'): continue
            p=graph.parsed(tid)
            errors.append({'task_id':tid,'stage':t.stage,'agent':t.context.get('agent'),
                           'attempts':len(store.attempts(tid)), 'parse_id':graph.selected.get(tid),
                           'reason':p['error'] if p else '传输结果未知且尝试次数耗尽'})
        materials=[{'operation':m['operation'],'status':m['status'],'reason':m['reason']}
                   for m in graph.materials if (m['dataset_id'],m['item_id'])==key and m['status']!='eligible']
        missing=[{k:r[k] for k in ('model_id','variant','protocol','round','status','reason')}
                 for r in rows if (r['dataset_id'],r['item_id'])==key and r['status']!='complete']
        store.event('item_skipped',dataset_id=key[0],item_id=key[1],
                    reason='重试/材料资格流程耗尽，本题仍缺真实终局结果',errors=errors,
                    materials=materials,missing_outputs=missing)
        recorded.append(key)
    return recorded


def failure_status(store, graph=None):
    from .matrix import result_matrix
    from .scheduler import Planner
    records=list(skipped_items(store).values())
    if not records: return {'skipped_items':0,'missing_outputs':0,'items':[]}
    rows=result_matrix(store,graph or Planner(store,persist=False).build())
    complete={key:True for key in skipped_items(store)}
    for r in rows:
        key=(r['dataset_id'],r['item_id'])
        if key in complete and r['status']!='complete': complete[key]=False
    records=[r for r in records if not complete[(r['dataset_id'],r['item_id'])]]
    return {'skipped_items':len(records),'missing_outputs':sum(len(r['missing_outputs']) for r in records),'items':records}


def export_failures(store):
    result=failure_status(store)
    name='failures-'+canonical_hash(result)[:16]
    with store.lock():
        store.put('reports',name,result)
        out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=('dataset_id','item_id','reason','errors','materials','missing_outputs'))
        writer.writeheader()
        for r in result['items']:
            writer.writerow({k:json.dumps(r[k],ensure_ascii=False) if isinstance(r[k],list) else r[k] for k in writer.fieldnames})
        store.write_blob('exports',name,out.getvalue().encode('utf-8'),'.csv')
    return {'summary':{k:v for k,v in result.items() if k!='items'},
            'json':str(store.path/'reports'/f'{name}.json'),'csv':str(store.path/'exports'/f'{name}.csv')}
