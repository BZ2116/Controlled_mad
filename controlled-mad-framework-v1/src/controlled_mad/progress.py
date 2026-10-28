"""只读的固定计划进度；逻辑槽位与实际请求次数分开统计。"""
from collections import Counter
from .protocol import item_settings

STAGES=('S0','generate','annotate','audit','T1','T2','T3')

def stages_for(config):
    return STAGES+('T4','T5') if config.get('experiment')=='rq2_five_round' else STAGES

def arm_counts(config,independent_rounds=3):
    if config.get('experiment')=='rq2_five_round':
        return Counter({f'T{round_no}':3 for round_no in range(1,6)})
    counts=Counter(T1=3,T2=3+3*(independent_rounds==3),T3=3+3*(independent_rounds==3))
    for rule in config['interventions']:
        if rule['delivery_round']==2: counts['T2']+=3
        counts['T3']+=3
    return counts

def progress_snapshot(store,graph,states):
    """等待项包含尚未生成或被无效上游阻断的槽位，不伪装成已取消。"""
    config=store.config
    stages_order=stages_for(config)
    rhetoric=sum(op in ('ethos','logos','pathos','neutral') for op in config['operations'])
    reorder=len(config['operations'])-rhetoric
    attempts=config['max_generation_attempts']
    audit_enabled=config.get('material_audit',True)
    from .protocol import STUDY_V2
    per=Counter(S0=3,generate=rhetoric*attempts,annotate=int(bool(reorder) and not config.get('complete_matrix') and config.get('study_protocol')!=STUDY_V2),audit=(rhetoric*attempts+reorder) if audit_enabled else 0)
    oo=config['diagnostics']['oo']
    if oo['enabled']:
        for stage,count in (('T1',3),('T2',6),('T3',6)):
            per[stage]+=count*2*oo['repeats']
    stage_states={stage:Counter() for stage in stages_order}
    group_states={group:{stage:Counter() for stage in stages_order} for group in
                  {(row['dataset_id'],row['item_id'],model) for row in store.get('inputs','items')['items'] for model in config['debate_models']}}
    per_group={}; natural_group={}; repetitions={}
    selected=(set(store.get('inputs','intervention-subset')['selected_item_ids'])
              if config.get('intervention_subset') else None)
    for group in group_states:
        settings=item_settings(config,getattr(graph,'amendment',None),group[0],group[1])
        repetitions[group]=settings['repeats']
        effective=config if selected is None or group[1] in selected else {**config,'interventions':[]}
        natural_group[group]=arm_counts(effective,settings['independent_rounds'])
        per_group[group]=per.copy()
        for stage,count in natural_group[group].items():
            per_group[group][stage]+=count*repetitions[group]*(1+len(config['operations']))
    planned=Counter({stage:sum(p[stage] for p in per_group.values()) for stage in stages_order})
    material_tasks=Counter()
    key=lambda ctx:(ctx['dataset_id'],ctx['item_id'],ctx['model_id'])
    resolved={key(m)+(m['operation'],) for m in graph.materials if m['status']=='eligible'}
    retired={group:Counter() for group in group_states}
    for tid,task in graph.tasks.items():
        superseded=(config.get('complete_matrix') and task.stage in ('generate','audit')
                    and states[tid]=='invalid' and key(task.context)+(task.context['operation'],) in resolved)
        if superseded:
            retired[key(task.context)][task.stage]+=1
        else:
            stage_states[task.stage][states[tid]]+=1
            group_states[key(task.context)][task.stage][states[tid]]+=1
        if task.stage in ('generate','audit'):
            material_tasks[key(task.context)+(task.context['operation'],task.stage)]+=1
    cancelled_by_group={}; materials={}
    for group in {(row['dataset_id'],row['item_id'],model) for row in store.get('inputs','items')['items'] for model in config['debate_models']}:
        cancelled_by_group[group]=retired[group].copy()
    for material in graph.materials:
        group=key(material)
        materials.setdefault(group,[]).append(material)
        if material['status']=='pending': continue
        op=material['operation']; maximum=attempts if op in ('ethos','logos','pathos') else 1
        material_stages=('generate','audit') if audit_enabled and op in ('ethos','logos','pathos') else (('generate',) if op in ('ethos','logos','pathos') else ('audit',) if audit_enabled else ())
        for stage in material_stages:
            cancelled_by_group[group][stage]+=maximum-material_tasks[group+(op,stage)]
        if material['status']!='eligible' and not config.get('complete_matrix'):
            for stage,count in natural_group[group].items(): cancelled_by_group[group][stage]+=count*repetitions[group]
    # A group with no eligible variants also skips its shared original arm.
    for group,records in materials.items():
        if not config.get('complete_matrix') and len(records)==len(config['operations']) and all(m['status'] not in ('pending','eligible') for m in records):
            for stage,count in natural_group[group].items(): cancelled_by_group[group][stage]+=count*repetitions[group]
    cancelled=Counter()
    for row in cancelled_by_group.values(): cancelled.update(row)
    stages={}
    for stage in stages_order:
        counts=stage_states[stage]
        waiting=planned[stage]-sum(counts.values())-cancelled[stage]
        if waiting<0: raise ValueError('进度槽位统计超出冻结计划')
        stages[stage]={'planned':planned[stage],**{s:counts[s] for s in ('succeeded','ready','invalid','transport_unknown')},
                       'cancelled':cancelled[stage],'waiting':waiting,'running':0}
    totals={field:sum(row[field] for row in stages.values()) for field in next(iter(stages.values()))}
    totals['settled']=totals['succeeded']+totals['cancelled']
    item_rows={}
    for dataset_id,item_id,_model in group_states:
        item_rows.setdefault((dataset_id,item_id),Counter())
    # Aggregate per-model planned slots and terminal state into question-level progress.
    for group in cancelled_by_group:
        dataset_id,item_id,_model=group
        target=item_rows.setdefault((dataset_id,item_id),Counter())
        for stage in stages_order:
            target['planned']+=per_group[group][stage]
            target['cancelled']+=cancelled_by_group[group][stage]
            for state in ('succeeded','ready','invalid','transport_unknown'):
                target[state]+=group_states[group][stage][state]
            target['waiting']+=per_group[group][stage]-cancelled_by_group[group][stage]-sum(group_states[group][stage][s] for s in ('succeeded','ready','invalid','transport_unknown'))
    item_order=[(row['dataset_id'],row['item_id']) for row in store.get('inputs','items')['items']]
    complete=sum(not any(row.get(state,0) for state in ('ready','waiting','invalid','transport_unknown','running')) for row in item_rows.values())
    items={'total':len(item_order),'complete':complete,'current':None,'position':None}
    if config.get('complete_matrix'):
        from .matrix import result_matrix, matrix_summary
        rows=result_matrix(store,graph)
        matrix=matrix_summary(rows)
        items.update(complete=matrix['complete_items'],complete_results=matrix['complete_outputs'],expected_results=matrix['expected_outputs'])
        from .failures import enabled, skipped_items
        skipped=skipped_items(store)
        if skipped or enabled(store):
            incomplete={(r['dataset_id'],r['item_id']) for r in rows if r['status']!='complete'}
            count=len(set(skipped)&incomplete)
            items.update(skipped=count,processed=matrix['complete_items']+count)
    return {**totals,'items':items,'stages':stages}

def transition(snapshot,stage,before,after):
    """每次请求只更新内存计数；下一轮从原始事件重建。"""
    for counts in (snapshot,snapshot['stages'][stage]):
        counts[before]-=1
        counts[after]+=1
    snapshot['settled']=snapshot['succeeded']+snapshot['cancelled']
