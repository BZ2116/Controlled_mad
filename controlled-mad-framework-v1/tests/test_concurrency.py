"""按题并发的等价性与崩溃恢复。

并发合法性来自 scheduler 的「题间无依赖」：task_id 是含 item_id 的内容寻址哈希。
本文件的第一个测试就是整个并发特性的前提——如果并发与串行的产物集合不等价，
后面所有测试都失去意义。

用例一律跑缩小后的题集：Windows 上每次落盘都 fsync，跑满 6 题的端到端要三分钟，
放进单测里会让整套测试慢到没人愿意等。完整 mock 全量跑属于验收闸门，不属于单测。
"""
import json, os, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest.mock import patch
from controlled_mad.engine import create_run, execute_ready, status, chargeable_attempts
from controlled_mad.scheduler import Planner
from controlled_mad.progress import progress_snapshot, transition
from controlled_mad.store import RunStore
from controlled_mad.budget import BudgetError
from controlled_mad.api import analyze_and_export
from controlled_mad.identity import canonical_bytes
from controlled_mad.domain import Task, ModelProfile
from controlled_mad.backends.mock import MockBackend
from controlled_mad.backends.openai_compatible import OpenAIBackend
from controlled_mad.backends.transport import TransportUnknown
ROOT=Path(__file__).resolve().parents[1]

# 并发不应影响这些内容寻址集合；事件序号与时间戳会变，所以不在此列。
CONTENT_COLLECTIONS=('tasks','parses','snapshots','materials','comparisons','messages','deliveries','results')

class Concurrency(unittest.TestCase):
    ITEMS=3
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=str(ROOT.parent/'.tmp')); self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        rows=(ROOT/'examples/toy_dataset.jsonl').read_text(encoding='utf-8').splitlines()[:self.ITEMS]
        self.dataset=self.path/'small.jsonl'
        self.dataset.write_text('\n'.join(rows)+'\n',encoding='utf-8')
        config=json.loads((ROOT/'configs/demo.json').read_text(encoding='utf-8'))
        config['dataset']['source']=str(self.dataset)
        config['mock_scenario']=str(ROOT/'examples/mock_scenario.json')
        config['operations']=['ethos','logos']
        config['interventions']=[]
        config['diagnostics']['oo']['enabled']=False
        self.config=config

    def build(self,name):
        path=self.path/f'{name}.json'; path.write_text(json.dumps(self.config),encoding='utf-8')
        return create_run(path,name,self.path)

    def test_concurrent_run_matches_sequential_run(self):
        """并发与串行必须产出完全相同的内容寻址对象集合与进度。"""
        serial=self.build('serial'); execute_ready(serial,concurrency=1)
        parallel=self.build('parallel'); execute_ready(parallel,concurrency=5)
        for collection in CONTENT_COLLECTIONS:
            self.assertEqual(set(serial.ids(collection)),set(parallel.ids(collection)),f'{collection} 集合不一致')
        a=status(serial)['progress']; b=status(parallel)['progress']
        for field in ('planned','succeeded','invalid','cancelled','waiting','settled'):
            self.assertEqual(a[field],b[field],f'progress.{field} 不一致')
        for field in ('total','complete'):
            self.assertEqual(a['items'][field],b['items'][field],f'progress.items.{field} 不一致')
        self.assertEqual(status(serial)['budget'],status(parallel)['budget'])
        self.assertEqual(analyze_and_export(serial)['denominators'],analyze_and_export(parallel)['denominators'])

    def test_worker_failure_propagates_to_caller(self):
        """worker 里的预算错误必须回到主线程，不能退化成静默的部分失败。"""
        run=self.build('worker-error')
        original=MockBackend.generate
        def exploding(backend,task,p):
            if task.context['item_id']=='toy-003': raise BudgetError('构造的预算耗尽')
            return original(backend,task,p)
        with patch.object(MockBackend,'generate',exploding):
            with self.assertRaises(BudgetError): execute_ready(run,concurrency=5)
        # 抛错前其他题已经落盘的调用必须留下痕迹，不能被回滚成"从未发生"
        self.assertGreater(status(run)['budget']['calls'],0)

    def test_transport_failure_in_one_item_does_not_lose_other_items(self):
        """单题连续传输失败不应牵连其他题已经拿到的结果。"""
        run=self.build('transport')
        original=MockBackend.generate
        def flaky(backend,task,p):
            if task.context['item_id']=='toy-002' and task.stage=='S0': raise TransportUnknown('模拟断连')
            return original(backend,task,p)
        with patch('controlled_mad.engine.time.sleep'):
            with patch.object(MockBackend,'generate',flaky):
                result=execute_ready(run,concurrency=5,auto_retry_unknown=True)
        self.assertEqual(result['stop'],'transport_unknown')
        summary=status(run)
        self.assertGreaterEqual(summary['budget']['calls'],5)
        # toy-002 的 3 个 S0 任务全部断连，逐个记为 unknown
        self.assertGreaterEqual(summary['transport_unknown'],1)
        self.assertGreaterEqual(summary['progress']['succeeded'],1)

    def test_crash_mid_round_recovers_without_duplicate_calls(self):
        """并发跑到一半进程暴毙：已提交 raw 要被 recover 捡回，续跑不得重发同一尝试。"""
        run=self.build('crash')
        execute_ready(run,max_calls=2,concurrency=1)
        task=next(t for t in Planner(run).build().tasks.values()
                  if t.context['item_id']=='toy-003' and t.stage=='S0')
        aid=f'{task.task_id}-a003'
        # 子进程命令行受 Windows 控制台代码页限制，脚本正文必须保持纯 ASCII。
        script="""import os,sys
from controlled_mad.store import RunStore
s=RunStore(sys.argv[1])
s.event('started',task_id=sys.argv[2],attempt_id=sys.argv[3],stage='S0',provider='manual',reserved_tokens=999,wire_format='text')
s.write_blob('raw',sys.argv[3],b'{"label":"A","comment":"committed-before-crash"}','.bin')
os._exit(29)
"""
        result=subprocess.run([sys.executable,'-c',script,str(run.path),task.task_id,aid],
                              env={**os.environ,'PYTHONPATH':str(ROOT/'src')},capture_output=True)
        self.assertEqual(result.returncode,29,result.stderr.decode('utf-8','replace'))
        reopened=RunStore(run.path)
        before=status(reopened)['budget']['calls']
        execute_ready(reopened,concurrency=5)
        recovered=reopened.result(task.task_id)
        self.assertIsNotNone(recovered,'已提交的 raw 应被 recover 捡回')
        self.assertEqual(recovered['value']['comment'],'committed-before-crash')
        self.assertEqual(len(reopened.attempts(task.task_id)),1,'不应为同一尝试再发一次请求')
        self.assertGreaterEqual(status(reopened)['budget']['calls'],before)

    def test_write_blob_same_key_from_many_threads(self):
        """exists()+open('xb') 的 TOCTOU：同键并发写必须走幂等分支而不是炸 FileExistsError。"""
        run=self.build('blob-race')
        errors=[]; barrier=threading.Barrier(5)
        def writer():
            try:
                barrier.wait()
                for _ in range(20): run.write_blob('exports','shared-key',canonical_bytes({'v':1}))
            except BaseException as exc: errors.append(exc)
        threads=[threading.Thread(target=writer) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors,[])
        self.assertEqual(run.get('exports','shared-key'),{'v':1})

    def test_transition_partition_stays_valid_under_interleaving(self):
        """多 worker 各迁自己的 task：整体与每阶段计数都不得为负，也不得超出计划。"""
        run=self.build('partition')
        graph=Planner(run).build()
        states={tid:'ready' for tid in graph.tasks}
        overall=progress_snapshot(run,graph,states)
        buckets={}
        for task in graph.tasks.values(): buckets.setdefault(task.context['item_id'],[]).append(task)
        errors=[]
        def worker(tasks):
            try:
                for task in tasks:
                    transition(overall,task.stage,'ready','running')
                    time.sleep(0)
                    transition(overall,task.stage,'running','succeeded')
            except BaseException as exc: errors.append(exc)
        threads=[threading.Thread(target=worker,args=(tasks,)) for tasks in buckets.values()]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(errors,[])
        for row in (overall,*overall['stages'].values()):
            self.assertEqual(row['planned'],sum(row[k] for k in ('succeeded','ready','invalid','transport_unknown','cancelled','waiting','running')))
            self.assertTrue(all(value>=0 for value in row.values() if isinstance(value,int)))

    def test_concurrency_must_be_positive_integer(self):
        run=self.build('bad-concurrency')
        for bad in (0,-1,'5',1.5,True):
            with self.assertRaises(ValueError): execute_ready(run,concurrency=bad)

    def test_one_worker_never_gets_two_items(self):
        """同一题不能被两个 worker 同时推进：按题去重是并发正确性的前提。

        不能用线程 id 判定——线程池每轮重建，OS 会回收并复用线程 id。
        也不看 wall/busy 比：Windows 上每次落盘都 fsync，串行写盘就能把比值撑开，
        那测的是磁盘不是并发。直接量「同一时刻有几道题正在 generate」。
        """
        run=self.build('one-item-one-worker')
        original=MockBackend.generate
        spans=[]; inflight=set(); peak=[0]; lock=threading.Lock()
        def tracking(backend,task,p):
            item=task.context['item_id']; start=time.perf_counter()
            with lock:
                inflight.add(item); peak[0]=max(peak[0],len(inflight))
            try:
                time.sleep(0.02)
                return original(backend,task,p)
            finally:
                with lock:
                    spans.append((item,start,time.perf_counter())); inflight.discard(item)
        with patch.object(MockBackend,'generate',tracking):
            execute_ready(run,concurrency=5)
        by_item={}
        for item,start,end in spans: by_item.setdefault(item,[]).append((start,end))
        self.assertEqual(len(by_item),self.ITEMS)
        for item,ranges in by_item.items():
            ranges.sort()
            for (first_end,_),(second_start,_) in zip(ranges,ranges[1:]):
                self.assertLessEqual(first_end,second_start,f'{item} 的两次调用出现时间重叠')
        self.assertGreaterEqual(peak[0],2,f'未观察到真实并发，峰值在飞题数为 {peak[0]}')

    def test_execution_policy_records_flags_without_touching_config_hash(self):
        serial=self.build('policy-1'); execute_ready(serial,max_calls=1,concurrency=1)
        parallel=self.build('policy-5'); execute_ready(parallel,max_calls=1,concurrency=5,retry_http=True)
        events=[e for e in parallel.all('events') if e['kind']=='execution_policy']
        self.assertEqual(events[-1]['concurrency'],5)
        self.assertTrue(events[-1]['retry_http'])
        # 执行策略不进冻结配置：同配置的两个运行 config_hash 必须一致
        self.assertEqual(serial.get('','manifest')['config_hash'],parallel.get('','manifest')['config_hash'])

    def test_http_429_stops_by_default(self):
        """默认关闭 --retry-http：HTTP 层失败必须变成停机信号，不能被静默重试。"""
        t=Task('t','S0','p',({'role':'user','content':'u'},),(),{},'h')
        p=ModelProfile('p','openai','m','https://example.test/v1','TEST_CREDENTIAL')
        with patch.dict(os.environ,{'TEST_CREDENTIAL':'dummy'}):
            result=OpenAIBackend(lambda *a:(429,{'Retry-After':'30'},b'rate limited')).generate(t,p)
        self.assertIn('429',result.error)

    def test_http_429_becomes_retryable_when_opted_in(self):
        """开启 --retry-http：408/429/5xx 抛 TransportUnknown，复用退避重试通路。"""
        t=Task('t','S0','p',({'role':'user','content':'u'},),(),{},'h')
        p=ModelProfile('p','openai','m','https://example.test/v1','TEST_CREDENTIAL')
        for status_code in (408,429,500,503):
            with patch.dict(os.environ,{'TEST_CREDENTIAL':'dummy'}):
                with self.assertRaises(TransportUnknown):
                    OpenAIBackend(lambda *a,code=status_code:(code,{'Retry-After':'1'},b'x'),retry_http=True).generate(t,p)
        # 401 这类配置错误不能当成可重试，否则会拿着坏凭据空转到退避上限
        with patch.dict(os.environ,{'TEST_CREDENTIAL':'dummy'}):
            result=OpenAIBackend(lambda *a:(401,{},b'nope'),retry_http=True).generate(t,p)
        self.assertIn('401',result.error)

    def test_rate_limited_attempt_is_not_charged_against_retry_quota(self):
        """被服务端拒绝的尝试不占 max_attempts，否则三次限流就会把整题永久 defer。"""
        run=self.build('rate-limit-quota')
        task=next(iter(Planner(run).build().tasks.values()))
        for number in (2,3):
            aid=f'{task.task_id}-a{number:03d}'
            run.event('retry_requested',task_id=task.task_id,attempt_id=aid,reason='限流退避')
            run.event('started',task_id=task.task_id,attempt_id=aid,stage=task.stage,provider='mock',reserved_tokens=100,wire_format='mock')
            run.event('transport_unknown',task_id=task.task_id,attempt_id=aid,reason='rate limited or server error')
        self.assertEqual(chargeable_attempts(run,task.task_id),0)
        # 预算侧仍然计这次真实请求：重试额度与调用预算是两个独立口径
        self.assertEqual(status(run)['budget']['calls'],2)

    def test_resume_does_not_change_content_addressed_sets(self):
        """中断续跑不得改变内容寻址集合。

        build() 每次重建都会重算 comparison 视图，若把半成品也落盘，集合就变成调度时序的
        函数：分几次跑完会比一次跑完多出一批中间版本。这条与并发无关，concurrency=1 同样犯。
        """
        once=self.build('resume-1shot'); execute_ready(once,concurrency=5)
        chunked=self.build('resume-chunked')
        while execute_ready(chunked,concurrency=5,max_calls=7)['stop']=='call_limit': pass
        for collection in CONTENT_COLLECTIONS:
            self.assertEqual(set(once.ids(collection)),set(chunked.ids(collection)),f'{collection} 集合不一致')
        self.assertEqual(status(once)['budget'],status(chunked)['budget'])

    def test_verify_and_analyze_pass_after_concurrent_run(self):
        run=self.build('verify')
        execute_ready(run,concurrency=5)
        report=analyze_and_export(run)
        self.assertIn('verified_objects',RunStore(run.path).verify())
        self.assertGreater(report['denominators']['complete_comparison_repeats'],0)
