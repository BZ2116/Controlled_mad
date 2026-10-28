"""Versioned study delivery: expression contrasts, initial outcomes and coverage."""
from collections import Counter, defaultdict
from .analysis import aggregate, metrics


def supplement(store,graph,gold,conditions):
    expressions=[]; grouped=defaultdict(dict)
    for row in conditions:
        grouped[tuple(row[k] for k in ('dataset_id','model_id','operation','protocol','round','design'))][row['variant']]=row
    for key,cells in sorted(grouped.items()):
        if not all(v in cells and cells[v]['vote_distribution'] is not None for v in ('O','R')): continue
        expressions.append({**dict(zip(('dataset_id','model_id','operation','protocol','round','design'),key)),
                            'items':cells['O']['complete_items'],
                            'E':{layer:{label:cells['R'][field][label]-cells['O'][field][label] for label in cells['O'][field]}
                                 for layer,field in (('vote','vote_distribution'),('verdict','verdict_distribution'))}})
    groups=defaultdict(lambda:defaultdict(list))
    for series in graph.series:
        ctx=series['context']
        if ctx['batch']!='main': continue
        initial=aggregate(ctx['initial_labels'],ctx['labels']); actual=gold.get((ctx['dataset_id'],ctx['item_id']))
        for protocol in ('I','M'):
            states=series['states'][protocol]
            if not all(states.values()): continue
            for t,snapshot in states.items():
                now=metrics(snapshot,ctx['labels']); before_correct=initial['verdict']==actual if actual is not None else None
                after_correct=now['verdict']==actual if actual is not None else None
                key=tuple(ctx[k] for k in ('dataset_id','model_id','variant'))+(protocol,int(t))
                groups[key][ctx['item_id']].append({'verdict_flipped':initial['verdict']!=now['verdict'],
                                                  'correct_to_wrong':before_correct and not after_correct if actual is not None else None,
                                                  'wrong_to_correct':not before_correct and after_correct if actual is not None else None})
    initial_outcomes=[]
    for key,items in sorted(groups.items()):
        row=dict(zip(('dataset_id','model_id','variant','protocol','round'),key))
        row['complete_items']=len(items)
        for field in ('verdict_flipped','correct_to_wrong','wrong_to_correct'):
            means=[]
            for repeats in items.values():
                values=[r[field] for r in repeats if r[field] is not None]
                if values: means.append(sum(values)/len(values))
            row[field+'_rate']=sum(means)/len(means) if means else None
            row[field+'_denominator']=len(means)
        row['denominator_scope']='complete arms; each item equal weight; available-condition diagnostic, not the paired primary denominator'
        initial_outcomes.append(row)
    input_items=store.get('inputs','items')['items']; coverage=[]
    for model in store.config['debate_models']:
        for op in store.config['operations']:
            records=[m for m in graph.materials if m['model_id']==model and m['operation']==op]
            counts=Counter(m['status'] for m in records)
            coverage.append({'model_id':model,'operation':op,'planned_items':len(input_items),'constructed_items':len(records),
                             'unconstructed_items':len(input_items)-len(records),'statuses':dict(counts)})
    # Whole-item eligibility depends on every operation, not a favorable final answer.
    eligibility=[]
    for model in store.config['debate_models']:
        counts=Counter()
        for item in input_items:
            records=[m for m in graph.materials if (m['dataset_id'],m['item_id'],m['model_id'])==(item['dataset_id'],item['item_id'],model)]
            valid=len(records)==len(store.config['operations']) and all(m['status']=='eligible' for m in records)
            counts[(str(gold.get((item['dataset_id'],item['item_id']))),'all_materials_eligible' if valid else 'not_all_eligible')]+=1
        eligibility.extend({'model_id':model,'gold':label,'eligibility':status,'items':n} for (label,status),n in sorted(counts.items()))
    return {'expression_effects':expressions,'initial_outcome_summaries':initial_outcomes,
            'material_coverage':coverage,'eligibility_by_gold':eligibility}


def noise_summaries(rows):
    grouped=defaultdict(lambda:defaultdict(list))
    for row in rows:
        if row['complete']:
            key=tuple(row[k] for k in ('dataset_id','model_id','protocol','round'))
            grouped[key][row['item_id']].append(row['batch_difference'])
    result=[]
    for key,items in sorted(grouped.items()):
        row=dict(zip(('dataset_id','model_id','protocol','round'),key));row['items']=len(items)
        row['verdict_disagreement_rate']=sum(sum(any(v!=0 for v in d['verdict'].values()) for d in repeats)/len(repeats) for repeats in items.values())/len(items)
        first=next(iter(items.values()))[0]
        row['mean_absolute_difference']={layer:{label:sum(sum(abs(d[layer][label]) for d in repeats)/len(repeats) for repeats in items.values())/len(items) for label in first[layer]} for layer in ('vote','verdict')}
        row['scope']='conditional on this control run frozen S0; descriptive noise diagnostic, never subtracted from main D'
        result.append(row)
    return result
