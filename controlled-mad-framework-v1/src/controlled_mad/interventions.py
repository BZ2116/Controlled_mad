from .domain import AGENTS, Intervention

def resolve_rule(rule,sender):
    r=Intervention(**rule)
    others=[a for a in AGENTS if a!=sender]
    mapping={'S':sender,'A':others[0],'B':others[1]}
    return {'id':r.rule_id,'source':mapping[r.source_agent],'target':mapping[r.target_agent],'round':r.delivery_round}
