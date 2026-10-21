from collections import Counter
from .identity import canonical_bytes
class BudgetError(RuntimeError): pass

def input_bound(task):
    # UTF-8 字节数 + 每消息固定开销，保守预留；不伪称精确 tokenizer。
    return len(canonical_bytes(task.messages))+64*len(task.messages)+256

def usage_tokens(usage):
    if not isinstance(usage,dict): return None
    if 'prompt_tokens' in usage and 'completion_tokens' in usage:
        values=[usage['prompt_tokens'],usage['completion_tokens']]
    elif 'input_tokens' in usage and 'output_tokens' in usage:
        values=[usage['input_tokens'],usage['output_tokens'],usage.get('cache_creation_input_tokens',0),usage.get('cache_read_input_tokens',0)]
    else: return None
    if any(type(x) is not int or x<0 for x in values): return None
    return sum(values)

class Budget:
    def __init__(self,store): self.store=store
    def totals(self):
        return self.store.budget_totals()
    def reserve(self,task,profile):
        totals=self.totals(); cap=self.store.config['budget']; needed=input_bound(task)+profile.max_tokens
        if needed>profile.context_limit: raise BudgetError('完整历史超过配置上下文预算，不截断')
        if totals['calls']+1>cap['max_calls'] or totals['tokens']+needed>cap['max_tokens'] or totals['stages'].get(task.stage,0)+1>cap['stage_calls'][task.stage]: raise BudgetError('调用或 token 预算不足')
        return needed
    def reconcile(self,attempt_id,result):
        n=usage_tokens(result.usage)
        if n is not None: self.store.event('usage',attempt_id=attempt_id,tokens=n)
