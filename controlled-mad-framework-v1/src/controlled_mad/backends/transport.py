import json, urllib.request, urllib.error
import math
from datetime import timezone
from email.utils import parsedate_to_datetime
class TransportUnknown(RuntimeError): pass
class RetryableHTTP(TransportUnknown):
    """已收到可重试 HTTP 响应；与服务是否收到请求未知的断连分开记账。"""
    pass

def retryable_status(status):
    return status in (408,429) or 500<=status<600

def retry_after_deadline(headers,received_at):
    value=next((v for k,v in headers.items() if k.lower()=='retry-after'),'')
    try:
        value=value.strip()
        if value.isascii() and value.isdigit():
            deadline=received_at+int(value)
        else:
            date=parsedate_to_datetime(value)
            if date.tzinfo is None: date=date.replace(tzinfo=timezone.utc)
            deadline=date.timestamp()
        return float(deadline) if math.isfinite(deadline) else None
    except (ValueError,TypeError,AttributeError,OverflowError):
        return None

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None

def http_post(url,payload,headers,timeout):
    req=urllib.request.Request(url,json.dumps(payload,ensure_ascii=False).encode(),headers,method='POST')
    try:
        with urllib.request.build_opener(NoRedirect).open(req,timeout=timeout) as r:
            return r.status,dict(r.headers),r.read()
    except urllib.error.HTTPError as e:
        return e.code,dict(e.headers),e.read()
    except (urllib.error.URLError,TimeoutError,OSError):
        raise TransportUnknown('传输结果未知，禁止自动重发') from None
