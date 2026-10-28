"""描述性双层四格估计，保留完整分母与物理执行身份。"""
from collections import Counter, defaultdict
from .scheduler import Planner
from .identity import canonical_hash
from .protocol import LEGACY, SINGLE_PASS


def aggregate(votes,labels):
    labels=tuple(labels)
    if len(votes)!=3 or any(v not in labels for v in votes):
        return {'status':'missing','verdict':None,'vote':None,'verdict_vector':None}
    counts=Counter(votes)
    verdict=next((k for k,n in counts.items() if n>=2),'no_majority')
    return {'status':'complete','verdict':verdict,'vote':{k:counts[k]/3 for k in labels},'verdict_vector':{k:float(k==verdict) for k in (*labels,'no_majority')}}

def metrics(snapshot,labels):
    return aggregate([r['value']['label'] for r in snapshot['replies']] if snapshot else [],labels)

def four_cell_difference(m_r,m_o,i_r,i_o):
    if any(x['status']!='complete' for x in (m_r,m_o,i_r,i_o)): return None
    result={}
    for layer,key in (('vote','vote'),('verdict','verdict_vector')):
        result[layer]={k:(m_r[key][k]-m_o[key][k])-(i_r[key][k]-i_o[key][k]) for k in m_r[key]}
    return result

def full_arm(arm):
    return (set(arm['I']) in ({'1'},{'1','2','3'}) and set(arm['M'])=={'1','2','3'}
            and all(arm[p][t] for p in ('I','M') for t in arm[p]))

def cell_snapshot(comparison,variant,protocol,round_no):
    """缩减设计将真实 I-T1 作为固定基线，不伪造 I-T2/T3 观测。"""
    states=comparison[variant][protocol]
    return states['1'] if protocol=='I' and set(states)=={'1'} else states[str(round_no)]

def initial_pattern(labels):
    counts=Counter(labels)
    return '3:0' if len(counts)==1 else ('2:1' if max(counts.values())==2 else '1:1:1')

def analyze(store,selection=None):
    graph=Planner(store,persist=False).build()
    gold={(r['dataset_id'],r['item_id']):r['label'] for r in store.get('analysis_inputs','gold')['gold']}
    rounds=[]; interventions=[]; pairs=[]; executions={}; transitions=[]; groups=defaultdict(list)
    for c in graph.comparisons:
        ctx=c['context']; complete=full_arm(c['O']) and full_arm(c['R'])
        design=ctx.get('design',LEGACY)
        pair={'comparison_id':c['comparison_id'],**ctx,'complete_case_pair':complete}
        pairs.append(pair)
        key=(ctx['dataset_id'],ctx['item_id'],ctx['model_id'],ctx['operation'],design)
        groups[key].append(c)
        for t in ('1','2','3'):
            cell={f'{p}-{v}':metrics(cell_snapshot(c,v,p,t),ctx['labels']) for p in ('I','M') for v in ('O','R')}
            d=four_cell_difference(cell['M-R'],cell['M-O'],cell['I-R'],cell['I-O']) if complete else None
            rounds.append({**pair,'design':design,'round':int(t),'independent_actual_round':1 if design==SINGLE_PASS else int(t),'cells':cell,'D':d})
        for rule in store.config['interventions']:
            rid=rule['rule_id']
            if rid not in c['O']['interventions']: continue
            io=c['O']['interventions'][rid]; ir=c['R']['interventions'][rid]
            valid=complete and all(io[t] and ir[t] for t in ('1','2','3'))
            for t in ('1','2','3'):
                mr=metrics(c['R']['M'][t],ctx['labels']); mo=metrics(c['O']['M'][t],ctx['labels'])
                rr=metrics(cell_snapshot(c,'R','I',t),ctx['labels']); ro=metrics(cell_snapshot(c,'O','I',t),ctx['labels'])
                natural=four_cell_difference(mr,mo,rr,ro) if valid else None
                changed=four_cell_difference(metrics(ir[t],ctx['labels']),metrics(io[t],ctx['labels']),rr,ro) if valid else None
                delta={layer:{k:changed[layer][k]-natural[layer][k] for k in natural[layer]} for layer in natural} if valid else None
                interventions.append({**pair,'rule_id':rid,'round':int(t),'complete_intervention_pair':valid,'natural_D':natural,'intervention_D':changed,'visibility_operation_delta':delta})
    snapshot_pairs=defaultdict(list)
    for c in graph.comparisons:
        ref={'comparison_id':c['comparison_id'],'natural_complete_case_pair':full_arm(c['O']) and full_arm(c['R'])}
        for variant in ('O','R'):
            for protocol in ('I','M'):
                for snapshot in c[variant][protocol].values():
                    if snapshot: snapshot_pairs[snapshot['snapshot_id']].append(ref)
            for states in c[variant]['interventions'].values():
                for snapshot in states.values():
                    if snapshot: snapshot_pairs[snapshot['snapshot_id']].append(ref)
    for s in graph.series:
        ctx=s['context']; label_space=ctx['labels']; actual_gold=gold.get((ctx['dataset_id'],ctx['item_id']))
        branches={p:states for p,states in s['states'].items() if p in ('I','M')}
        branches.update({f'M:{k}':v for k,v in s['states']['interventions'].items()})
        for branch,states in branches.items():
            for t,snapshot in states.items():
                if not snapshot: continue
                sid=snapshot['snapshot_id']; m=metrics(snapshot,label_space)
                initial=aggregate(ctx['initial_labels'],label_space)
                executions[sid]={'snapshot_id':sid,**snapshot['context'],'round':int(t),**m,'gold':actual_gold,
                    'correct':None if actual_gold is None else m['verdict']==actual_gold,'physical_reply_ids':[r['parse_id'] for r in snapshot['replies']],
                    'initial_pattern':initial_pattern(ctx['initial_labels']),'initial_verdict':initial['verdict'],
                    'initial_correct':None if actual_gold is None else initial['verdict']==actual_gold,'verdict_flipped':m['verdict']!=initial['verdict']}
            for t in ('2','3'):
                if t not in states or str(int(t)-1) not in states: continue
                before=states[str(int(t)-1)]; after=states[t]
                if not before or not after: continue
                previous={r['agent']:r for r in before['replies']}
                for r in after['replies']:
                    transitions.append({**ctx,'branch':branch,'round':int(t),'agent':r['agent'],'from_label':previous[r['agent']]['value']['label'],'to_label':r['value']['label'],
                        'denominator_scope':'available_case','comparison_pair_refs':snapshot_pairs[after['snapshot_id']],
                        'from_parse':previous[r['agent']]['parse_id'],'to_parse':r['parse_id']})
            first=states['1']
            if first:
                for index,r in enumerate(first['replies']):
                    transitions.append({**ctx,'branch':branch,'round':1,'agent':r['agent'],
                        'from_label':ctx['initial_labels'][index],'to_label':r['value']['label'],
                        'denominator_scope':'available_case','comparison_pair_refs':snapshot_pairs[first['snapshot_id']],
                        'from_parse':ctx['source_ids'][index],'to_parse':r['parse_id']})
    effects=[]
    for key,comparisons in groups.items():
        usable=[c for c in comparisons if full_arm(c['O']) and full_arm(c['R'])]
        for t in ('1','2','3'):
            values=[four_cell_difference(metrics(cell_snapshot(c,'R','M',t),c['context']['labels']),metrics(cell_snapshot(c,'O','M',t),c['context']['labels']),metrics(cell_snapshot(c,'R','I',t),c['context']['labels']),metrics(cell_snapshot(c,'O','I',t),c['context']['labels'])) for c in usable]
            mean={layer:{k:sum(v[layer][k] for v in values)/len(values) for k in values[0][layer]} for layer in ('vote','verdict')} if values else None
            effects.append({'dataset_id':key[0],'item_id':key[1],'model_id':key[2],'operation':key[3],'design':key[4],'round':int(t),'initial_pattern':initial_pattern(comparisons[0]['context']['initial_labels']),'planned_repeats':len(comparisons),'complete_repeats':len(usable),'D':mean})
    summaries=[]
    grouped=defaultdict(list)
    for effect in effects:
        if effect['D'] is not None: grouped[(effect['dataset_id'],effect['model_id'],effect['operation'],effect['round'],effect['design'])].append(effect)
    for key,values in grouped.items():
        mean={layer:{k:sum(v['D'][layer][k] for v in values)/len(values) for k in values[0]['D'][layer]} for layer in ('vote','verdict')}
        summaries.append({'dataset_id':key[0],'model_id':key[1],'operation':key[2],'round':key[3],'design':key[4],'items':len(values),'D':mean})
    stratified=[]; strata=defaultdict(list)
    for effect in effects:
        if effect['D'] is not None: strata[(effect['dataset_id'],effect['model_id'],effect['operation'],effect['round'],effect['initial_pattern'],effect['design'])].append(effect)
    for key,values in strata.items():
        mean={layer:{k:sum(v['D'][layer][k] for v in values)/len(values) for k in values[0]['D'][layer]} for layer in ('vote','verdict')}
        stratified.append({'dataset_id':key[0],'model_id':key[1],'operation':key[2],'round':key[3],'initial_pattern':key[4],'design':key[5],'items':len(values),'D':mean})
    oo=[]
    diagnostic_groups=defaultdict(dict)
    for s in graph.series:
        if s['context']['batch'].startswith('oo_'):
            ctx=s['context']; key=(ctx['dataset_id'],ctx['item_id'],ctx['model_id'],ctx['repeat'])
            diagnostic_groups[key][ctx['batch']]=s['states']
    for key,batches in diagnostic_groups.items():
        valid=all(b in batches and full_arm(batches[b]) for b in ('oo_1','oo_2'))
        for p in ('I','M'):
            for t in ('1','2','3'):
                difference=None
                if valid:
                    labels=batches['oo_1'][p][t]['context']['labels']
                    a=metrics(batches['oo_1'][p][t],labels); b=metrics(batches['oo_2'][p][t],labels)
                    difference={'vote':{k:b['vote'][k]-a['vote'][k] for k in a['vote']},'verdict':{k:b['verdict_vector'][k]-a['verdict_vector'][k] for k in a['verdict_vector']}}
                oo.append({'dataset_id':key[0],'item_id':key[1],'model_id':key[2],'repeat':key[3],'protocol':p,'round':int(t),'complete':valid,'batch_difference':difference})
    source=store.get('inputs','items')
    complete_executions=list(executions.values())
    correct=[r['correct'] for r in complete_executions if r['correct'] is not None]
    denominators={'input_items':len(source['items']),'rejected_items':len(source['rejected']),
        'item_model_s0_slots':len(source['items'])*len(store.config['debate_models']),
        'frozen_s0_count':len({m['s0_hash'] for m in graph.materials}),
        'material_status':dict(Counter(m['status'] for m in graph.materials)),
        'comparison_repeats':len(pairs),'complete_comparison_repeats':sum(p['complete_case_pair'] for p in pairs),
        'unique_complete_round_snapshots':len(executions),'gold_missing_snapshots':sum(r['gold'] is None for r in complete_executions),
        'no_majority_snapshots':sum(r['verdict']=='no_majority' for r in complete_executions),
        'pooled_diagnostic_accuracy':sum(correct)/len(correct) if correct else None,
        'accuracy_note':'pooled_diagnostic_accuracy 混合了轮次和分支，仅作运行诊断；研究结论使用分条件 execution_rows。'}
    vote_rows=[]; deliveries=[]
    for task in graph.tasks.values():
        p=graph.parsed(task.task_id)
        if p and task.stage in ('S0','T1','T2','T3'):
            vote_rows.append({'task_id':task.task_id,**task.context,'valid':p['valid'],'parse_id':graph.selected[task.task_id],'value':p['value'],'error':p['error']})
        if task.stage in ('T1','T2','T3'):
            deliveries.append({'task_id':task.task_id,'prompt_hash':task.prompt_hash,**task.context})
    from .outcomes import paired_outcomes
    from .inference import infer_effects
    extra=paired_outcomes(graph.comparisons,gold)
    if store.config.get('complete_matrix'):
        from .matrix import result_matrix, matrix_summary
        matrix=result_matrix(store,graph)
        extra.update(result_matrix=matrix,matrix_summary=matrix_summary(matrix,store),matrix_layout=store.config.get('matrix_layout','slot_first_v1'))
        from .failures import enabled, failure_status
        if enabled(store):
            failures=failure_status(store,graph)
            extra.update(failures=failures['items'],failure_summary={k:v for k,v in failures.items() if k!='items'})
        if (store.path/'inputs'/'matrix-reuse-complete.json').exists():
            extra['matrix_reuse']=store.get('inputs','matrix-reuse-complete')
    from .protocol import STUDY_V2
    modern=store.config.get('study_protocol')==STUDY_V2
    if modern:
        # Keep the declared family even when a failed S0/material yielded no comparison.
        present={(r['dataset_id'],r['item_id'],r['model_id'],r['operation'],r['round']) for r in effects}
        for item in source['items']:
            for model in store.config['debate_models']:
                for op in store.config['operations']:
                    for t in (1,2,3):
                        key=(item['dataset_id'],item['item_id'],model,op,t)
                        if key not in present:
                            effects.append(dict(zip(('dataset_id','item_id','model_id','operation','round'),key),
                                                design=store.config.get('matrix_protocol',LEGACY),initial_pattern='unavailable',
                                                planned_repeats=1,complete_repeats=0,D=None))
        from .study_analysis import supplement,noise_summaries
        from .visibility import audit_visibility
        extra.update(supplement(store,graph,gold,extra['condition_summaries']))
        extra['noise_summaries']=noise_summaries(oo)
        routing=audit_visibility(store,graph)
        if not routing['valid']:
            from .store import IntegrityError
            raise IntegrityError('实际消息来源与冻结可见性计划不符，拒绝生成正常实验报告')
        extra.update(study_protocol=STUDY_V2,visibility_audit=routing,
                     execution_provenance={'providers':sorted({e.get('provider','unknown') for e in store.all('events') if e['kind']=='started'})},
                     model_profiles=store.config['profiles'])
        if store.config.get('coding'):
            from .coding import coding_report
            extra['coding_agreement']=coding_report(store,graph)
        if store.config.get('material_validation'):
            from .material_validation import report as validation_report
            extra['material_validation']=validation_report(store,graph)
    if store.config.get('inference'): extra['inference']=infer_effects(effects,store.config['inference'],version=STUDY_V2 if modern else 'legacy_v1')
    from .protocol import load_amendment
    return {'schema_version':1,'protocol_amendment':load_amendment(store),**extra,'denominators':denominators,'votes':vote_rows,'deliveries':deliveries,'pairs':pairs,'round_effects':rounds,'item_effects':effects,'summary_effects':summaries,'stratified_effects':stratified,
        'intervention_effects':interventions,'oo_diagnostics':oo,'execution_rows':complete_executions,'transitions':transitions,
        'materials':graph.materials,'attempts':store.all('events'),'rejected_items':source['rejected']}
