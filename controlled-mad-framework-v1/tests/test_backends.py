import json, unittest
from unittest.mock import patch
from controlled_mad.domain import Task, ModelProfile
from controlled_mad.backends.openai_compatible import OpenAIBackend
from controlled_mad.backends.anthropic_compatible import AnthropicBackend

class Backends(unittest.TestCase):
    def test_openai_contract(self):
        calls=[]
        def transport(url,payload,headers,timeout):
            calls.append((url,payload,headers))
            return 200,{},json.dumps({'model':'m','choices':[{'message':{'content':'OK'},'finish_reason':'stop'}],'usage':{'prompt_tokens':3,'completion_tokens':2}}).encode()
        t=Task('t','S0','p',({'role':'user','content':'hello'},),(),{},'h')
        with patch.dict('os.environ',{'TEST_CREDENTIAL':'dummy'}):
            result=OpenAIBackend(transport).generate(t,ModelProfile('p','openai','m','https://example.test/v1','TEST_CREDENTIAL'))
        self.assertEqual(result.text,'OK'); self.assertEqual(calls[0][0],'https://example.test/v1/chat/completions')
        self.assertNotIn('dummy',result.raw.decode())
    def test_openai_reasoning_effort_is_forwarded(self):
        calls=[]
        def transport(url,payload,headers,timeout):
            calls.append((url,payload,headers))
            return 200,{},json.dumps({'model':'MiniMax-M3.1-Flash','choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]}).encode()
        t=Task('t','S0','p',({'role':'user','content':'hello'},),(),{},'h')
        profile=ModelProfile('p','openai','MiniMax-M3.1-Flash','https://api.minimax.cn/v1','TEST_CREDENTIAL',reasoning_effort='max')
        with patch.dict('os.environ',{'TEST_CREDENTIAL':'dummy'}):
            result=OpenAIBackend(transport).generate(t,profile)
        self.assertIsNone(result.error)
        self.assertEqual(calls[0][0],'https://api.minimax.cn/v1/chat/completions')
        self.assertEqual(calls[0][1]['model'],'MiniMax-M3.1-Flash')
        self.assertEqual(calls[0][1]['reasoning_effort'],'max')
        self.assertEqual(calls[0][2]['Authorization'],'Bearer dummy')
    def test_anthropic_text_only(self):
        def transport(url,payload,headers,timeout):
            self.assertEqual(payload['system'],'system')
            self.assertEqual(headers['anthropic-version'],'2023-06-01')
            return 200,{},json.dumps({'model':'m','content':[{'type':'thinking','thinking':'hidden'},{'type':'text','text':'final'}],'stop_reason':'end_turn','usage':{'input_tokens':1,'output_tokens':1}}).encode()
        t=Task('t','S0','p',({'role':'system','content':'system'},{'role':'user','content':'u'}),(),{},'h')
        with patch.dict('os.environ',{'TEST_CREDENTIAL':'dummy'}):
            r=AnthropicBackend(transport).generate(t,ModelProfile('p','anthropic','m','https://example.test/v1','TEST_CREDENTIAL'))
        self.assertEqual(r.text,'final')
    def test_invalid_response_keeps_raw(self):
        t=Task('t','S0','p',({'role':'user','content':'u'},),(),{},'h')
        with patch.dict('os.environ',{'TEST_CREDENTIAL':'dummy'}):
            r=OpenAIBackend(lambda *args:(429,{'Retry-After':'30'},b'bad dummy')).generate(t,ModelProfile('p','openai','m','https://example.test/v1','TEST_CREDENTIAL'))
        self.assertIn('429',r.error); self.assertNotIn(b'dummy',r.raw)
