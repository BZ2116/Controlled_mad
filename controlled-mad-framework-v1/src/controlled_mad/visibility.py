"""处理前冻结的角色级消息矩阵；实际消息 ID 由 deliveries 逐条审计。"""


def visibility_matrix(config):
    roles = ('S', 'A', 'B')
    five = config.get('experiment') == 'rq2_five_round'
    branches = ['M'] if five else ['I', 'M'] + [r['rule_id'] for r in config['interventions']]
    rules = {r['rule_id']: r for r in config['interventions']}
    rows = []
    for branch in branches:
        last=1 if branch=='I' and config.get('complete_matrix') and config.get('matrix_protocol')!='equal_rounds_v10' else (5 if five else 3)
        for t in range(1, last+1):
            for recipient in roles:
                sources = [r for r in roles if r != recipient]
                blocked = []
                if branch == 'I' and t > 1: sources = []
                rule = rules.get(branch)
                if rule and rule['delivery_round'] == t and rule['target_agent'] == recipient:
                    blocked = [rule['source_agent']]
                    sources.remove(rule['source_agent'])
                rows.append({'branch': branch, 'round': t, 'recipient': recipient,
                             'source_round': t - 1, 'new_sources': sources, 'blocked_new_sources': blocked,
                             'retains_previous_history': True, 'expression_versions': ['O', 'R'],
                             'note': 'S0 at source_round=0; no same-round messages; retransmission by other agents remains possible'})
    from .protocol import STUDY_V2
    if config.get('study_protocol')==STUDY_V2:
        shared=[]
        for recipient in roles:
            first=next(r for r in rows if r['round']==1 and r['recipient']==recipient)
            shared.append({**first,'branch':'shared','logical_branches':branches})
        rows=shared+[r for r in rows if r['round']>1]
    return rows


def audit_visibility(store,graph):
    """Compare selected physical replies against the frozen role-level plan."""
    from .domain import AGENTS
    plan=store.get('inputs','visibility-plan')['matrix']
    indexed={(r['branch'],r['round'],r['recipient']):r for r in plan}
    checked=0; violations=[]
    for task in graph.tasks.values():
        if task.stage not in ('T1','T2','T3') or task.task_id not in graph.selected: continue
        checked+=1; ctx=task.context
        role={'S':ctx['sender']}; rest=[a for a in AGENTS if a!=ctx['sender']]
        role.update(A=rest[0],B=rest[1]); inverse={v:k for k,v in role.items()}
        branch='shared' if ctx['round']==1 else (ctx['protocol'] if ctx['branch']=='natural' else ctx['branch'])
        rule=indexed.get((branch,ctx['round'],inverse[ctx['agent']]))
        errors=[]
        if rule is None: errors.append('no_frozen_rule')
        else:
            actual=[]; blocked=[]
            for field,target in (('delivered',actual),('blocked',blocked)):
                for mid in ctx[field]:
                    message=store.get('messages',mid)
                    target.append(inverse[message['sender']])
                    if message['round_no']!=rule['source_round']: errors.append('wrong_source_round')
            if sorted(actual)!=sorted(rule['new_sources']): errors.append('wrong_delivered_roles')
            if sorted(blocked)!=sorted(rule['blocked_new_sources']): errors.append('wrong_blocked_roles')
        if errors: violations.append({'task_id':task.task_id,'errors':errors})
    return {'checked_selected_replies':checked,'violations':violations,'valid':not violations,
            'scope':'physical shared T1; frozen role routes and source rounds; history content remains available in trace'}
