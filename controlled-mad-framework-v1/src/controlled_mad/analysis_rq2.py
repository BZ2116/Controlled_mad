"""五轮自然 MAD 配对轨迹；独立于三轮四格估计。"""
from collections import Counter, defaultdict
import random
from .scheduler import Planner
from .analysis import metrics, initial_pattern

def difference(right,left):
    if right['status']!='complete' or left['status']!='complete': return None
    return {'vote':{k:right['vote'][k]-left['vote'][k] for k in right['vote']},
            'verdict':{k:right['verdict_vector'][k]-left['verdict_vector'][k] for k in right['verdict_vector']}}

def mean_deltas(values):
    if not values: return None
    return {layer:{label:sum(v[layer][label] for v in values)/len(values) for label in values[0][layer]}
            for layer in ('vote','verdict')}

def analyze_rq2(store):
    graph=Planner(store,persist=False).build()
    gold={(r['dataset_id'],r['item_id']):r['label'] for r in store.get('analysis_inputs','gold')['gold']}
    pairs=[]; rounds=[]; late_changes=[]; executions={}; transitions=[]; repeat_groups=defaultdict(list)
    for comparison in graph.comparisons:
        ctx=comparison['context']; labels=ctx['labels']
        original=comparison['O']['M']; rewritten=comparison['R']['M']
        complete=all(original[str(t)] and rewritten[str(t)] for t in range(1,6))
        pair={'comparison_id':comparison['comparison_id'],**ctx,'complete_five_round_pair':complete}
        pairs.append(pair)
        repeat_groups[(ctx['dataset_id'],ctx['item_id'],ctx['model_id'],ctx['operation'])].append(comparison)
        for t in range(1,6):
            o=metrics(original[str(t)],labels); r=metrics(rewritten[str(t)],labels)
            rounds.append({**pair,'round':t,'is_final':t==5,'M_O':o,'M_R':r,
                           'available_pair':o['status']=='complete' and r['status']=='complete',
                           'main_denominator':complete,'delta':difference(r,o) if complete else None,
                           'available_case_delta':difference(r,o)})
        if complete:
            t3={arm:metrics(comparison[arm]['M']['3'],labels) for arm in ('O','R')}
            t5={arm:metrics(comparison[arm]['M']['5'],labels) for arm in ('O','R')}
            d3=difference(t3['R'],t3['O']); d5=difference(t5['R'],t5['O'])
            late_changes.append({**pair,'T3_to_T5_O':difference(t5['O'],t3['O']),
                'T3_to_T5_R':difference(t5['R'],t3['R']),
                'paired_delta_change':{layer:{label:d5[layer][label]-d3[layer][label] for label in d3[layer]}
                                       for layer in ('vote','verdict')}})
    for series in graph.series:
        ctx=series['context']; states=series['states']['M']; actual_gold=gold.get((ctx['dataset_id'],ctx['item_id']))
        for t in range(1,6):
            snapshot=states[str(t)]
            if snapshot is None: continue
            m=metrics(snapshot,ctx['labels']); sid=snapshot['snapshot_id']
            executions[sid]={'snapshot_id':sid,**ctx,'round':t,'is_final':t==5,**m,'gold':actual_gold,
                             'correct':None if actual_gold is None else m['verdict']==actual_gold,
                             'replies':snapshot['replies']}
            if t==1 or states[str(t-1)] is None: continue
            previous={r['agent']:r for r in states[str(t-1)]['replies']}
            for reply in snapshot['replies']:
                old=previous[reply['agent']]
                transitions.append({**ctx,'round':t,'agent':reply['agent'],'from_label':old['value']['label'],
                    'to_label':reply['value']['label'],'changed':old['value']['label']!=reply['value']['label'],
                    'from_parse':old['parse_id'],'to_parse':reply['parse_id'],'denominator_scope':'available_case'})
    item_effects=[]
    for key,comparisons in repeat_groups.items():
        usable=[c for c in comparisons if all(c[arm]['M'][str(t)] for arm in ('O','R') for t in range(1,6))]
        for t in range(1,6):
            values=[difference(metrics(c['R']['M'][str(t)],c['context']['labels']),
                               metrics(c['O']['M'][str(t)],c['context']['labels'])) for c in usable]
            item_effects.append({'dataset_id':key[0],'item_id':key[1],'model_id':key[2],'operation':key[3],
                'round':t,'is_final':t==5,'initial_pattern':initial_pattern(comparisons[0]['context']['initial_labels']),
                'planned_repeats':len(comparisons),'complete_repeats':len(usable),'delta':mean_deltas(values),
                'repeat_deltas':values,'repeat_range':None if len(values)<2 else
                    {layer:{label:max(v[layer][label] for v in values)-min(v[layer][label] for v in values)
                            for label in values[0][layer]} for layer in ('vote','verdict')}})
    summary_effects=[]; grouped=defaultdict(list)
    for row in item_effects:
        if row['delta'] is not None: grouped[(row['dataset_id'],row['model_id'],row['operation'],row['round'])].append(row)
    for key,items in grouped.items():
        values=[row['delta'] for row in items]
        estimate=mean_deltas(values)
        ci=None
        if len(values)>=2:
            rng=random.Random(20261021)
            draws=[mean_deltas([values[rng.randrange(len(values))] for _ in values]) for _ in range(1000)]
            ci={layer:{label:[sorted(draw[layer][label] for draw in draws)[24],sorted(draw[layer][label] for draw in draws)[974]]
                       for label in estimate[layer]} for layer in ('vote','verdict')}
        summary_effects.append({'dataset_id':key[0],'model_id':key[1],'operation':key[2],
            'round':key[3],'is_final':key[3]==5,'items':len(items),'delta':estimate,'item_bootstrap_95_interval':ci})
    selected=store.get('inputs','subset'); source=store.get('inputs','items')
    denominators={'candidate_items':selected['candidate_items'],'selected_items':len(source['items']),
        'selection_hash':selected['selection_hash'],'rejected_items':len(source['rejected']),
        'material_status':dict(Counter(m['status'] for m in graph.materials)),
        'comparison_repeats':len(pairs),'complete_five_round_pairs':sum(p['complete_five_round_pair'] for p in pairs),
        'available_round_pairs':{str(t):sum(r['round']==t and r['available_pair'] for r in rounds) for t in range(1,6)},
        'missing_round_pairs':{str(t):sum(r['round']==t and not r['available_pair'] for r in rounds) for t in range(1,6)},
        'no_majority_snapshots':sum(r['verdict']=='no_majority' for r in executions.values())}
    votes=[]; deliveries=[]
    for task in graph.tasks.values():
        parsed=graph.parsed(task.task_id)
        if parsed and task.stage in ('S0','T1','T2','T3','T4','T5'):
            votes.append({'task_id':task.task_id,**task.context,'valid':parsed['valid'],
                'parse_id':graph.selected[task.task_id],'value':parsed['value'],'error':parsed['error']})
        if task.stage in ('T1','T2','T3','T4','T5'):
            deliveries.append({'task_id':task.task_id,'prompt_hash':task.prompt_hash,**task.context})
    return {'schema_version':1,'experiment':'rq2_five_round','denominators':denominators,
        'pairs':pairs,'round_effects':rounds,'late_changes':late_changes,'item_effects':item_effects,'summary_effects':summary_effects,
        'execution_rows':list(executions.values()),'transitions':transitions,'votes':votes,
        'deliveries':deliveries,'materials':graph.materials,'attempts':store.all('events'),
        'rejected_items':source['rejected']}
