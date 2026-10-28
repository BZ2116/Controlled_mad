"""离线准备位置规则v2；只复用完全兼容的不可变回复，旧运行只读。"""
from importlib.resources import files
from pathlib import Path

from .config import load_config
from .identity import canonical_hash, plain, safe_id
from .store import RunStore

CHANGED = {'support_first', 'limitation_first'}


def _compatible(task):
    stage=task['stage']; ctx=task['context']
    if stage in ('S0','generate'): return True
    if stage=='audit': return ctx['operation'] not in CHANGED
    return (stage in ('T1','T2','T3') and ctx['variant'] not in CHANGED
            and ctx['repeat']==0 and ctx['batch']=='main')


def prepare_revision(config_path, source_run_id, run_id, runs_root, dry_run=False):
    """不读取凭据，不调用后端；相同命令可恢复未完成的离线导入。"""
    from .api import create_run
    from .datasets.base import adapter_for
    from .subset import select_bundle
    from .scheduler import Planner
    from .matrix import result_matrix
    from .pipeline import enable_item_pipeline

    safe_id(source_run_id); safe_id(run_id)
    if source_run_id==run_id: raise ValueError('必须使用新的v2运行ID，不能覆盖旧运行')
    root=Path(runs_root).resolve()
    source=RunStore(root/source_run_id); config=load_config(config_path)
    old=source.config
    without_layout=lambda c:{k:v for k,v in c.items() if k!='matrix_layout'}
    expansion=(old.get('complete_matrix') and config.get('complete_matrix')
               and old.get('matrix_layout')==config.get('matrix_layout')=='group_first_v2')
    excluded=set() if expansion else CHANGED
    if expansion:
        protocol=lambda c:{k:v for k,v in c.items() if k not in ('subset','budget')}
        if canonical_hash(protocol(old))!=canonical_hash(protocol(config)):
            raise ValueError('扩展题集只允许修改subset及增加预算，不得修改实验协议')
        if (config['budget']['max_calls']<old['budget']['max_calls'] or config['budget']['max_tokens']<old['budget']['max_tokens']
                or any(config['budget']['stage_calls'].get(k,0)<v for k,v in old['budget']['stage_calls'].items())):
            raise ValueError('扩展实验预算不得小于来源预算')
    elif (not old.get('complete_matrix') or old.get('matrix_layout','slot_first_v1')!='slot_first_v1'
            or config.get('matrix_layout')!='group_first_v2'
            or canonical_hash(without_layout(old))!=canonical_hash(without_layout(config))):
        raise ValueError('只能复用完全相同配置的位置规则v1→v2；其他模型、种子、预算或输入变更均拒绝')
    bundle=adapter_for(config['dataset']['adapter']).load(config['dataset']['source'])
    if config.get('subset'): bundle,_=select_bundle(bundle,config['subset'])
    original_input=source.get('inputs','items')
    target_items=plain(bundle.items)
    source_items=original_input['items']
    if (bundle.source_hash!=original_input['source_hash'] or
            canonical_hash(target_items[:len(source_items)] if expansion else target_items)!=canonical_hash(source_items)
            or (expansion and len(target_items)<len(source_items))):
        raise ValueError('当前输入已偏离旧运行冻结的输入，拒绝复用')
    if expansion:
        from .matrix import matrix_summary
        summary=matrix_summary(result_matrix(source,Planner(source,persist=False).build()))
        if summary['complete_items']!=len(source_items):
            raise ValueError('扩展来源必须每题16格完整，不能继承未验收子集')
    expected={}
    for name in ('s0','debate','rhetoric','audit','annotate'):
        override=config.get('prompts',{}).get(name)
        suffix='matrix-v1' if name in ('s0','rhetoric') else 'v1'
        expected[name]=((Path(config_path).resolve().parent/override).read_text(encoding='utf-8-sig') if override
                        else files('controlled_mad').joinpath('resources',f'{name}.{suffix}.txt').read_text(encoding='utf-8'))
    if expected!=source.get('inputs','prompts'):
        raise ValueError('当前提示与旧运行冻结提示不同，拒绝复用')
    target_path=root/run_id
    previous=None
    if target_path.exists():
        target=RunStore(target_path)
        if canonical_hash(target.config)!=canonical_hash(config): raise ValueError('目标运行配置不一致')
        if (target_path/'inputs'/'matrix-reuse-source.json').exists():
            previous=target.get('inputs','matrix-reuse-source')
            if previous['source_run_id']!=source_run_id: raise ValueError('目标已绑定不同来源')
        elif any(e['kind']!='initialized' for e in target.all('events')):
            raise ValueError('目标已有执行记录，请选择新的v2运行ID')
    all_events=source.all('events')
    boundary=previous['source_event_boundary'] if previous else len(all_events)
    if boundary>len(all_events): raise ValueError('来源历史事件缺失')
    events=all_events[:boundary]
    selected={}; last={}; finished=set()
    for e in events:
        if e['kind']=='selected': selected[e['task_id']]=e['parse_id']
        elif e['kind']=='started': last[e['task_id']]=e['attempt_id']
        elif e['kind']=='finished': finished.add(e['attempt_id'])
    tasks={tid:source.get('tasks',tid) for tid in selected}
    tasks={tid:task for tid,task in tasks.items() if (expansion or _compatible(task)) and last.get(tid) in finished}
    for task in tasks.values():
        if any(dependency not in tasks for dependency in task['dependencies']):
            raise ValueError('来源兼容任务缺少已完成的前置任务，拒绝不完整继承')
    attempts={e['attempt_id'] for e in events if e['kind']=='started' and e['task_id'] in tasks}
    history=[e for e in events if e.get('task_id') in tasks or e.get('attempt_id') in attempts]
    parses={e['parse_id'] for e in history if e.get('parse_id')}
    provenance={'mode':'completed_subset_expansion_v2' if expansion else 'compatible_layout_v1_to_v2','source_run_id':source_run_id,
                'source_config_hash':canonical_hash(old),'target_config_hash':canonical_hash(config),
                'source_event_boundary':boundary,'task_ids':sorted(tasks),'excluded_variants':sorted(excluded)}
    if previous and previous!=provenance: raise ValueError('来源或目标协议偏离已冻结的导入记录')
    # Compatible terminal replies are cached, but the new per-item material gate remains in force.
    cached=sum(r['status']=='complete' and r['variant'] not in excluded
               and set(r['task_ids'])<=set(tasks) and set(r['parse_ids'])<=parses
               for r in result_matrix(source,Planner(source,persist=False).build()))
    result={'phase':'preview' if dry_run else 'prepared','source_run_id':source_run_id,'run_id':run_id,'path':str(target_path),
            'imported_tasks':len(tasks),'imported_requests':len(attempts),'cached_terminal_outputs':cached,
            'new_api_calls':0,'excluded_variants':sorted(excluded)}
    if dry_run: return result
    if not target_path.exists(): target=create_run(config_path,run_id,root)
    with target.lock():
        target.put('inputs','matrix-reuse-source',provenance)
        if (target.path/'inputs'/'matrix-reuse-complete.json').exists():
            return target.get('inputs','matrix-reuse-complete')
        if any(e['kind'] in ('started','selected','finished','usage','retry_requested') and not e.get('matrix_reuse')
               for e in target.all('events')):
            raise ValueError('目标导入尚未完成却已有本地执行记录，拒绝覆盖选定回复；请使用新的运行ID')
        for tid,task in tasks.items(): target.put('tasks',tid,task)
        for aid in attempts:
            for collection,key,suffix in (('raw',aid,'.bin'),('results',aid,'.json'),('attempts',aid+'.receipt','.json')):
                if (source.path/collection/(key+suffix)).exists():
                    target.write_blob(collection,key,source.read_blob(collection,key,suffix),suffix)
        for pid in parses: target.write_blob('parses',pid,source.read_blob('parses',pid))
        imported={e['matrix_reuse']['event_sequence'] for e in target.all('events')
                  if e.get('matrix_reuse',{}).get('source_run_id')==source_run_id}
        for event in history:
            if event['sequence'] in imported: continue
            payload={k:v for k,v in event.items() if k not in ('kind','time','sequence','matrix_reuse')}
            origin={'source_run_id':source_run_id,'event_sequence':event['sequence'],'event_time':event['time']}
            if event.get('matrix_reuse'): origin['upstream']=event['matrix_reuse']
            target.event(event['kind'],**payload,matrix_reuse=origin)
        if not any(e['kind']=='revision' and e.get('matrix_source')==source_run_id for e in target.all('events')):
            target.event('revision',matrix_source=source_run_id,reason='复用兼容回复；此序号之后才计为新增请求')
        enable_item_pipeline(target)
        Planner(target).build()
        target.put('inputs','matrix-reuse-complete',result)
        return result
