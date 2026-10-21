from .base import HTTPBackend
class AnthropicBackend(HTTPBackend):
    def request(self,task,p,key):
        payload=self.parameters(task,p)
        payload['messages']=[m for m in task.messages if m['role']!='system']
        system='\n'.join(m['content'] for m in task.messages if m['role']=='system')
        if system: payload['system']=system
        return p.base_url.rstrip('/')+'/messages',payload,{'Content-Type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01'}
    def extract(self,obj):
        blocks=obj['content']
        if any(b.get('type')=='tool_use' for b in blocks): raise ValueError('不能把工具调用当裁决')
        return '\n'.join(b['text'] for b in blocks if b.get('type')=='text'),obj['stop_reason']
