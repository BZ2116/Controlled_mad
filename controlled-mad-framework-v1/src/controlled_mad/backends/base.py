import json, os
from typing import Protocol
from ..domain import Task, ModelProfile, ModelResult
from .transport import http_post, RetryableHTTP, retryable_status
class Backend(Protocol):
    def generate(self,task:Task,profile:ModelProfile)->ModelResult: ...

class HTTPBackend:
    def __init__(self,transport=http_post,on_raw=None,retry_http=False):
        self.transport=transport; self.on_raw=on_raw; self.retry_http=retry_http
    def generate(self,task,profile):
        key=os.environ.get(profile.credential_env)
        if not key: raise ValueError(f'凭据环境变量未设置：{profile.credential_env}')
        url,payload,headers=self.request(task,profile,key)
        status,response_headers,raw=self.transport(url,payload,headers,profile.timeout)
        raw=raw.replace(key.encode(),b'[REDACTED]')
        response_headers={k:v.replace(key,'[REDACTED]') for k,v in response_headers.items() if k.lower()=='retry-after'}
        if self.on_raw: self.on_raw(raw,status,response_headers)
        return self.decode_received(raw,profile,status,response_headers)
    def decode_received(self,raw,profile,status=200,response_headers=None):
        response_headers=response_headers or {}
        if status!=200:
            retry=response_headers.get('Retry-After',response_headers.get('retry-after',''))
            # 默认把 HTTP 层失败当作 stop 的信号：服务出问题时静默重试会掩盖真问题，
            # 还可能把限流伪装成材料质量不合格。只有显式开启 --retry-http 才交给退避重试。
            if self.retry_http and retryable_status(status):
                raise RetryableHTTP(f'HTTP {status}; Retry-After={retry}')
            return ModelResult(raw,'',None,None,None,f'HTTP {status}; Retry-After={retry}')
        return self.decode(raw,profile)
    def decode(self,raw,profile):
        try:
            obj=json.loads(raw)
            text,stop=self.extract(obj)
            model=obj.get('model')
            error=None
            if not isinstance(text,str) or not text.strip(): error='回复没有最终文本'
            elif stop not in ('stop','end_turn','stop_sequence'): error=f'回复未正常结束：{stop}'
            elif not model or (profile.expected_model and model!=profile.expected_model): error='返回模型标识不匹配或缺失'
            return ModelResult(raw,text if isinstance(text,str) else '',model,stop,obj.get('usage'),error)
        except (ValueError,KeyError,TypeError,IndexError,AttributeError):
            return ModelResult(raw,'',None,None,None,'服务响应结构无效')
    def parameters(self,task,p):
        d={'model':p.model,'max_tokens':p.max_tokens}
        if p.temperature is not None: d['temperature']=p.temperature
        if p.reasoning_effort is not None: d['reasoning_effort']=p.reasoning_effort
        if p.seed is not None:
            if not p.supports_seed: raise ValueError('模型不支持 seed')
            from ..identity import canonical_hash
            d['seed']=int(canonical_hash([p.seed,task.context.get('sampling_identity')])[:8],16)
        return d
