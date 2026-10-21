from .base import HTTPBackend
class OpenAIBackend(HTTPBackend):
    def request(self,task,p,key):
        payload=self.parameters(task,p)
        if p.max_tokens_field!='max_tokens': payload[p.max_tokens_field]=payload.pop('max_tokens')
        payload['messages']=list(task.messages)
        return p.base_url.rstrip('/')+'/chat/completions',payload,{'Content-Type':'application/json','Authorization':'Bearer '+key}
    def extract(self,obj):
        c=obj['choices']
        if len(c)!=1: raise ValueError('要求单一回复')
        message=c[0]['message']
        if message.get('tool_calls') or message.get('refusal'): raise ValueError('非正常裁决文本')
        return message['content'],c[0]['finish_reason']
