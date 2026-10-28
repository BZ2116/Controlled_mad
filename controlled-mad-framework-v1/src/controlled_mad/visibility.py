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
    return rows
