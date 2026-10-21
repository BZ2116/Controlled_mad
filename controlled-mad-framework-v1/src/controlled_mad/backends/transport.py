import json, urllib.request, urllib.error
class TransportUnknown(RuntimeError): pass
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
