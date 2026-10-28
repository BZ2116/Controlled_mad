"""Preselected, outcome-blinded human manipulation checks. No inferred annotations."""
from collections import Counter, defaultdict
from .identity import canonical_hash, safe_id
from .coding import validate_evidence

FIELDS=('facts_preserved','reasoning_preserved','scope_preserved','uncertainty_preserved')
STYLES=('ethos','logos','pathos')
VALUES=('yes','no','uncertain')
CODEBOOK={'version':'material-validation-v2','semantic_fields':list(FIELDS),'values':list(VALUES),
          'style_scores':{'values':[1,2,3,4,5],'fields':list(STYLES),
                          'meaning':'分别评原文和改写：ethos=可信审慎表达；logos=既有推理的表达清晰度；pathos=情绪唤起。不要把新内容当作风格增强。'},
          'procedure':'coder-a/coder-b 独立完成语义保留四字段和两文本三类风格分数；每字段逐字引文与说明。不向标注者显示操作、模型、金标、裁决或效应。',
          'limits':'文本本身可能暴露操纵，非完全盲法；人工核查估计保留率，不证明所有材料语义等价；不自动裁决分歧或删除研究结果。'}


def validation_id(material):
    return canonical_hash([material['material_id'],CODEBOOK['version']])


def packet(store,graph):
    selected=set(store.get('inputs','material-validation-plan')['selected_item_ids'])
    result=[]
    for m in graph.materials:
        if m['item_id'] not in selected or not m.get('rewritten'): continue
        result.append({'validation_id':validation_id(m),'item_id':m['item_id'],
                       'original':m['original'],'rewritten':m['rewritten'],
                       'character_counts':{'original':len(m['original']),'rewritten':len(m['rewritten'])},
                       'word_counts':{'original':len(m['original'].split()),'rewritten':len(m['rewritten'].split())},
                       'codes':{},'style_scores':{},'codebook_version':CODEBOOK['version']})
    return sorted(result,key=lambda r:r['validation_id'])


def records(store):
    rows=[r for key in store.ids('analysis_inputs') if key.startswith('material-validation-batch-') for r in store.get('analysis_inputs',key)['rows']]
    return list({(r['validation_id'],r['coder_id']):r for r in rows}.values())


def validate(rows,materials,prior):
    by_id={r['validation_id']:r for r in materials}
    old={(r['validation_id'],r['coder_id']):r for r in prior}; seen=set()
    if not rows: raise ValueError('人工材料验证批次为空')
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'validation_id','coder_id','codes','style_scores','rationale'}: raise ValueError('材料编码字段不符')
        safe_id(row['coder_id'])
        reference=by_id.get(row['validation_id'])
        if reference is None: raise ValueError('未知或已失效材料验证ID')
        if not isinstance(row['codes'],dict) or set(row['codes'])!=set(FIELDS) or any(v not in VALUES for v in row['codes'].values()): raise ValueError('语义四字段须为 yes/no/uncertain')
        scores=row['style_scores']
        if not isinstance(scores,dict) or set(scores)!={'original','rewritten'}: raise ValueError('必须分别对原文和改写评分')
        for values in scores.values():
            if not isinstance(values,dict) or set(values)!=set(STYLES) or any(type(v) is not int or not 1<=v<=5 for v in values.values()): raise ValueError('三种风格分数须为1至5整数')
        validate_evidence(row['rationale'],(*FIELDS,*STYLES),{'original':reference['original'],'rewritten':reference['rewritten']})
        key=(row['validation_id'],row['coder_id'])
        if key in seen or (key in old and row!=old[key]): raise ValueError('重复或冲突的材料验证记录不可覆盖')
        seen.add(key)
    return rows


def report(store,graph):
    materials=packet(store,graph); ids={r['validation_id'] for r in materials}
    indexed={(r['validation_id'],r['coder_id']):r for r in records(store) if r['validation_id'] in ids}
    pairs=[(i,indexed[i,'coder-a'],indexed[i,'coder-b']) for i in sorted(ids) if (i,'coder-a') in indexed and (i,'coder-b') in indexed]
    fields={}; disagreements=[]
    for field in FIELDS:
        n=len(pairs); a=Counter(x['codes'][field] for _,x,y in pairs); b=Counter(y['codes'][field] for _,x,y in pairs)
        observed=sum(x['codes'][field]==y['codes'][field] for _,x,y in pairs)/n if n else None
        expected=sum(a[v]*b[v] for v in VALUES)/n**2 if n else None
        fields[field]={'pairs':n,'agreement':observed,'cohen_kappa':(observed-expected)/(1-expected) if n and expected<1 else None,
                       'coder_a_distribution':dict(a),'coder_b_distribution':dict(b)}
        disagreements.extend({'validation_id':i,'field':field,'coder-a':x['codes'][field],'coder-b':y['codes'][field]} for i,x,y in pairs if x['codes'][field]!=y['codes'][field])
    operation={validation_id(m):(m['model_id'],m['operation']) for m in graph.materials}
    groups=defaultdict(list)
    for identifier,a,b in pairs: groups[operation[identifier]].append((a,b))
    summaries=[]; selected=store.get('inputs','material-validation-plan')['selected_item_ids']
    for model,op in sorted((m,op) for m in store.config['debate_models'] for op in store.config['operations']):
        values=groups[(model,op)];n=len(values)
        categories={field:{'coder_a_distribution':dict(Counter(a['codes'][field] for a,b in values)),
                           'coder_b_distribution':dict(Counter(b['codes'][field] for a,b in values)),
                           'both_yes':sum(a['codes'][field]==b['codes'][field]=='yes' for a,b in values),
                           'both_no':sum(a['codes'][field]==b['codes'][field]=='no' for a,b in values),
                           'both_uncertain':sum(a['codes'][field]==b['codes'][field]=='uncertain' for a,b in values),
                           'disagreements':sum(a['codes'][field]!=b['codes'][field] for a,b in values)} for field in FIELDS}
        summaries.append({'model_id':model,'operation':op,'planned_pairs':len(selected),
                          'available_pairs':sum(operation[i]==(model,op) for i in ids),'double_coded_pairs':n,
                          'both_raters_preserve_all':sum(all(a['codes'][f]==b['codes'][f]=='yes' for f in FIELDS) for a,b in values),
                          'semantic_fields':categories,
                          'mean_style_changes':{s:sum((a['style_scores']['rewritten'][s]-a['style_scores']['original'][s]+b['style_scores']['rewritten'][s]-b['style_scores']['original'][s])/2 for a,b in values)/n if n else None for s in STYLES}})
    return {'codebook_version':CODEBOOK['version'],'selected_items':len(selected),
            'expected_pairs':len(selected)*len(store.config['operations'])*len(store.config['debate_models']),
            'available_pairs':len(materials),'double_coded':len(pairs),
            'missing_by_coder':{c:sum((i,c) not in indexed for i in ids) for c in ('coder-a','coder-b')},
            'fields':fields,'unresolved_disagreements':disagreements,'operation_summaries':summaries,
            'interpretation':'独立人审尚未完成时保持缺失；双人都判保留不是语义等价证明；不能根据结果好坏事后挑材料。'}
