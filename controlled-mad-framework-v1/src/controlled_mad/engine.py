"""所有阶段共用的执行、提交和恢复入口。默认不连接真实服务。"""
import json, os, shutil, threading, time
from concurrent import futures
from importlib.resources import files
from collections import Counter
from pathlib import Path
from .domain import Task, ModelResult
from .identity import plain, canonical_hash, canonical_bytes
from .config import load_config, profile
from .store import RunStore, IntegrityError
from .scheduler import Planner
from .budget import Budget, BudgetError
from .parsing import parse_vote, json_object, parse_audit
from .material_checks import validate_blocks
from .datasets.base import adapter_for
from .subset import select_bundle
from .backends.mock import MockBackend
from .backends.openai_compatible import OpenAIBackend
from .backends.anthropic_compatible import AnthropicBackend
from .backends.transport import TransportUnknown, RetryableHTTP, retryable_status, retry_after_deadline
from .progress import progress_snapshot, transition

ROOT=Path(__file__).resolve().parents[2]

# 服务端明确拒绝或未受理的尝试不消耗任务重试额度：请求没有生效，凭空扣掉 max_attempts
# 会让该题被永久 defer，白白从四格分析里消失。预算侧仍照常计这次真实请求。
NON_CHARGEABLE_FAILURES=('backend rejected configuration','rate limited or server error')

def create_run(config_path,run_id,root=None):
    c=load_config(config_path)
    adapter=adapter_for(c['dataset']['adapter'])
    bundle=adapter.load(c['dataset']['source'])
    subset_record=None
    if c.get('subset'): bundle,subset_record=select_bundle(bundle,c['subset'])
    if not bundle.items: raise ValueError('数据集中没有可运行题目，请检查拒绝分母')
    names=('s0','debate','rhetoric','audit','annotate')
    overrides=c.get('prompts',{})
    if set(overrides)-set(names): raise ValueError('未知提示名称')
    templates={name:(Path(config_path).resolve().parent/overrides[name]).read_text(encoding='utf-8-sig') if name in overrides else files('controlled_mad').joinpath('resources',f'{name}.v1.txt').read_text(encoding='utf-8') for name in names}
    from .protocol import STUDY_V2
    if c.get('complete_matrix') or c.get('study_protocol')==STUDY_V2:
        for name in ('s0','rhetoric'):
            if name not in overrides:
                templates[name]=files('controlled_mad').joinpath('resources',f'{name}.matrix-v1.txt').read_text(encoding='utf-8')
    if c.get('study_protocol')==STUDY_V2 and c['operations']==['neutral'] and 'rhetoric' not in overrides:
        templates['rhetoric']=files('controlled_mad').joinpath('resources','rhetoric.neutral-v2.txt').read_text(encoding='utf-8')
    if c.get('experiment')=='rq2_five_round' and 'debate' not in overrides:
        templates['debate']=files('controlled_mad').joinpath('resources','debate.rq2-five-round.txt').read_text(encoding='utf-8')
    scenario=json.loads(Path(c['mock_scenario']).read_text(encoding='utf-8-sig')) if c.get('mock_scenario') else {'items':{}}
    used=set([*c['debate_models'],c['material_model']])
    if c.get('material_audit',True): used.add(c['audit_model'])
    for p in (c['profiles'][name] for name in used):
        if p.get('provider','mock')=='mock' and any(i.item_id not in scenario['items'] for i in bundle.items): raise ValueError('mock 场景未覆盖输入题目')
    store=RunStore.create(root or Path.cwd()/'runs',run_id,c,source_hash=bundle.source_hash)
    with store.lock():
        store.put('inputs','items',{'items':plain(bundle.items),'rejected':plain(bundle.rejected),'source_hash':bundle.source_hash})
        if subset_record is not None: store.put('inputs','subset',subset_record)
        store.put('analysis_inputs','gold',{'gold':plain(bundle.gold)})
        store.put('inputs','prompts',templates)
        store.put('inputs','mock',scenario)
        if c.get('material_policy')=='strict_v10':
            from .visibility import visibility_matrix
            store.put('inputs','visibility-plan',{'matrix':visibility_matrix(c)})
        for name,setting in (('coding-plan','coding'),('intervention-subset','intervention_subset'),('material-validation-plan','material_validation')):
            if c.get(setting):
                _,record=select_bundle(bundle,c[setting])
                if setting=='coding':
                    from .coding import CODEBOOK
                    from .coding import CODEBOOK_V2
                    record['codebook']=CODEBOOK_V2 if c.get('study_protocol')==STUDY_V2 else CODEBOOK
                    if c.get('study_protocol')==STUDY_V2 and c['coding'].get('replies_per_item') is not None:
                        from .coding import planned_reply_units
                        record['planned_reply_units']=sorted(planned_reply_units(c,plain(bundle.items),set(record['selected_item_ids'])))
                if setting=='material_validation':
                    from .material_validation import CODEBOOK as MATERIAL_CODEBOOK
                    record['codebook']=MATERIAL_CODEBOOK
                store.put('inputs',name,record)
        store.write_blob('inputs','items',b''.join(canonical_bytes(i)+b'\n' for i in bundle.items),'.jsonl')
        store.write_blob('analysis_inputs','gold',b''.join(canonical_bytes(g)+b'\n' for g in bundle.gold),'.jsonl')
        store.event('initialized',source_hash=bundle.source_hash)
        Planner(store).build()
    return store

def backend_for(store,task,on_raw=None,scenario=None,retry_http=False):
    provider=profile(store.config,task.profile_id).provider
    if provider=='mock': return MockBackend(scenario if scenario is not None else store.get('inputs','mock'),on_raw)
    backend=OpenAIBackend if provider=='openai' else AnthropicBackend
    return backend(on_raw=on_raw,retry_http=retry_http)

def load_task(store,task_id):
    t=store.get('tasks',task_id)
    return Task(t['task_id'],t['stage'],t['profile_id'],tuple(t['messages']),tuple(t['dependencies']),t['context'],t['prompt_hash'])

def parse_result(task,result,version):
    if result.error: return {'valid':False,'value':None,'error':result.error}
    try:
        if task.stage in ('S0','T1','T2','T3','T4','T5','generate'):
            parsed=parse_vote(result.text,tuple(task.context['labels']),version)
            if parsed['valid'] and task.stage=='S0' and task.context.get('comment_structure'):
                from .matrix import structured_blocks
                structured_blocks(parsed['value']['comment'])
            return parsed
        if task.stage=='annotate':
            value=json_object(result.text)
            if set(value)!={'blocks'} or not isinstance(value['blocks'],list): raise ValueError('原文注释缺少 blocks')
            validate_blocks(value['blocks'])
        elif task.stage=='audit': value=parse_audit(result.text)
        else: raise ValueError('未知阶段')
        return {'valid':True,'value':value,'error':None}
    except (ValueError,TypeError,KeyError) as e:
        return {'valid':False,'value':None,'error':str(e)}

def _save_parse(store,task,result,attempt_id,version='vote-v1',select=False):
    parsed={**parse_result(task,result,version),'task_id':task.task_id,'attempt_id':attempt_id,'parser_version':version,'raw_hash':canonical_hash(result.raw.hex()),'backend_error':result.error}
    pid=canonical_hash(parsed); store.put('parses',pid,parsed)
    if select: store.event('selected',task_id=task.task_id,parse_id=pid,attempt_id=attempt_id,reason='initial attempt or explicit repair')
    return {**parsed,'parse_id':pid}

def record_result(store,task,result,attempt_id,version='vote-v1'):
    store.write_blob('raw',attempt_id,result.raw,'.bin')
    summary={'text':result.text,'returned_model':result.returned_model,'stop_reason':result.stop_reason,'usage':result.usage,'error':result.error,'task_id':task.task_id,'attempt_id':attempt_id}
    store.put('results',attempt_id,summary)
    prior=store.selected_parse(task.task_id)
    parsed=_save_parse(store,task,result,attempt_id,version,select=prior is None)
    store.event('finished',task_id=task.task_id,attempt_id=attempt_id,state='succeeded' if parsed['valid'] else 'invalid',parse_id=parsed['parse_id'])
    Budget(store).reconcile(attempt_id,result)
    return parsed

def task_state(store,task_id,events=None,selected=None):
    starts,request,finished=store.task_events(task_id)
    parse_id=store.selected_parse(task_id) if selected is None else selected.get(task_id)
    if request and (not starts or starts[-1]['attempt_id']!=request['attempt_id']): return 'ready'
    if starts:
        if not finished: return 'transport_unknown'
        if parse_id:
            return 'succeeded' if store.get('parses',parse_id)['valid'] else 'invalid'
        return 'invalid'
    if parse_id: return 'succeeded' if store.get('parses',parse_id)['valid'] else 'invalid'
    return 'ready'

def chargeable_attempts(store,task_id,preflight_failures=None):
    """旧版在请求前失败也写入 started；这类尝试不占网络重试额度。"""
    if preflight_failures is None:
        preflight_failures={e['attempt_id'] for e in store.all('events') if e['kind']=='transport_unknown'
                            and e.get('reason') in NON_CHARGEABLE_FAILURES}
    return sum(a['attempt_id'] not in preflight_failures for a in store.attempts(task_id))

def check_credential(p):
    if p.provider!='mock' and not os.environ.get(p.credential_env):
        raise ValueError(f'凭据环境变量未设置：{p.credential_env}；请先在当前终端加载密钥，再续跑')

def _record_http_retry(store,task,aid,receipt):
    # 恢复可反复调用；已存在的失败记录和等待截止时间不能每次续跑都向后推。
    if any(e['kind']=='transport_unknown' and e.get('attempt_id')==aid
           and e.get('reason')=='rate limited or server error' for e in store.all('events')):
        return
    deadline=retry_after_deadline(receipt['headers'],receipt.get('received_at',time.time()))
    store.event('transport_unknown',task_id=task.task_id,attempt_id=aid,
                reason='rate limited or server error',http_status=receipt['status'],
                **({'retry_not_before':deadline} if deadline is not None else {}))


def recover(store,progress=None):
    store._ensure_events(progress=progress)
    recovered=[]; events=store.all('events'); selected=store.selection()
    finished={e['attempt_id'] for e in events if e['kind']=='finished'}
    for start in (e for e in events if e['kind']=='started' and e['attempt_id'] not in finished):
        aid=start['attempt_id']; task=load_task(store,start['task_id'])
        receiptfile=store.path/'attempts'/f'{aid}.receipt.json'
        receipt=store.get('attempts',aid+'.receipt') if receiptfile.exists() else None
        # 新尝试记录请求时的策略；老尝试回溯其开始前最后一条 execution_policy。
        policy=next((e for e in reversed(events) if e['kind']=='execution_policy' and e['sequence']<start['sequence']),{})
        retry_http=start.get('retry_http',policy.get('retry_http',False))
        if receipt and retry_http and retryable_status(receipt['status']):
            _record_http_retry(store,task,aid,receipt)
            continue
        rawfile=store.path/'raw'/f'{aid}.bin'
        if not rawfile.exists(): continue
        raw=store.read_blob('raw',aid,'.bin')
        if (store.path/'results'/f'{aid}.json').exists():
            d=store.get('results',aid); result=ModelResult(raw,d['text'],d['returned_model'],d['stop_reason'],d['usage'],d['error'])
        else:
            if start.get('wire_format')=='text': result=ModelResult(raw,raw.decode('utf-8'),None,'manual',None)
            else:
                backend=backend_for(store,task); p=profile(store.config,task.profile_id)
                if receipt and p.provider!='mock':
                    result=backend.decode_received(raw,p,receipt['status'],receipt['headers'])
                else: result=backend.decode(raw,p)
        record_result(store,task,result,aid)
        recovered.append(aid)
    return recovered

def execute_ready(store,allow_network=False,max_calls=None,backend_override=None,progress=None,auto_retry_unknown=False,concurrency=1,retry_http=False,item_pipeline=False,single_pass=False,question_batch_size=None,continue_on_error=False):
    """按题并发、题内串行地推进任务状态机。

    并发的合法性来自 scheduler 的「题间无依赖」：task_id 是含 item_id 的内容寻址哈希，
    因此并发与串行产出的 task/parse/snapshot/material/comparison 集合完全一致。

    三条不变量，任何改动都必须维持：
    1. 一个 worker 独占一道题，题内任务严格按序；绝不把同一题拆给多个 worker。
    2. HTTP 调用不持 store 互斥锁；预算预留、started 事件、结果落盘各自成临界区。
    3. 每轮只在调度段算一次 overall 快照，且必须等所有 worker join 后才开下一轮，
       否则 transition() 会跨快照边界把计数推成负数。
    """
    if type(concurrency) is not int or concurrency<1: raise ValueError('并发度必须是正整数')
    if single_pass and store.config.get('matrix_protocol')=='equal_rounds_v10':
        raise ValueError('等轮次完整矩阵不能启用单轮I协议变更')
    if question_batch_size is not None and (type(question_batch_size) is not int or question_batch_size<1 or not store.config.get('complete_matrix')):
        raise ValueError('题目批次大小必须为正整数，且仅用于完整矩阵实验')
    completed=[0]
    issued=[0]
    # 连续失败是全运行口径：6 次连续未决说明服务不可用，而不是这道题的材料不合格。
    # 一旦任何题成功就清零，因此单题反复重试不会把别的题一起拖停。
    failures=[0]
    slot_lock=threading.Lock()
    deferred=set()
    overall=None
    graph=None
    refreshed=set()
    stop_reason=None
    item_key=lambda task:(task.context['dataset_id'],task.context['item_id'])
    item_order={(row['dataset_id'],row['item_id']):index for index,row in enumerate(store.get('inputs','items')['items'])}
    scenario=store.get('inputs','mock')
    def report(event,**details):
        if progress is not None: progress({'event':event,'completed':completed[0],'overall':overall,**details})
    def run_item(key,tasks,states,preflight_failures):
        """题内串行执行一道题的 ready 任务；返回 (stop_reason, task_id, detail) 或 None。"""
        for task in tasks:
            p=profile(store.config,task.profile_id)
            if backend_override=='mock' and p.provider!='mock': raise ValueError('不能用 mock 替换冻结的真实后端')
            if p.provider!='mock' and not (allow_network and store.config['network_enabled']): raise BudgetError('真实调用需要冻结网络开关及 --allow-network')
            check_credential(p)
            delay=0
            retry_unknown=states.get(task.task_id)=='transport_unknown'
            with store._mutex:
                attempts=store.attempts(task.task_id)
                if retry_unknown:
                    delay=min(30,5*2**min(3,max(0,len(attempts)-1)))
                if attempts:
                    latest=attempts[-1]['attempt_id']
                    failure=next((e for e in reversed(store.all('events')) if e['kind']=='transport_unknown' and e.get('attempt_id')==latest),{})
                    delay=max(delay,failure.get('retry_not_before',0)-time.time())
            if delay:
                if continue_on_error and delay>60:
                    return ('transport_unknown',task.task_id,'服务要求等待超过60秒；已保存截止时间，稍后续跑')
                # 退避必须在锁外睡，否则会把其他 worker 的落盘一起堵住。
                report('retry_wait',seconds=delay,stage=task.stage,item_id=task.context.get('item_id',''),agent=task.context.get('agent',''))
                time.sleep(delay)
            with store._mutex:
                if max_calls is not None:
                    with slot_lock:
                        if issued[0]>=max_calls: return None
                if retry_unknown:
                    store.event('retry_requested',task_id=task.task_id,attempt_id=f'{task.task_id}-a{len(store.attempts(task.task_id))+1:03d}',reason='传输异常自动退避重试')
                    transition(overall,task.stage,'transport_unknown','ready')
                reserved=Budget(store).reserve(task,p)
                if chargeable_attempts(store,task.task_id,preflight_failures)>=store.config['max_attempts']: raise BudgetError('该槽位已达到尝试上限')
                aid=f'{task.task_id}-a{len(store.attempts(task.task_id))+1:03d}'
                store.event('started',task_id=task.task_id,attempt_id=aid,stage=task.stage,provider=p.provider,reserved_tokens=reserved,wire_format=p.provider,retry_http=retry_http)
                with slot_lock: issued[0]+=1
                transition(overall,task.stage,'ready','running')
                report('start',stage=task.stage,item_id=task.context.get('item_id',''),agent=task.context.get('agent',''),
                       task_id=task.task_id,item_position=item_order[key]+1)
            def commit_received(raw,http_status,headers):
                with store._mutex:
                    store.put('attempts',aid+'.receipt',{'status':http_status,'headers':headers,'received_at':time.time()})
                    store.write_blob('raw',aid,raw,'.bin')
            try: result=backend_for(store,task,on_raw=commit_received,scenario=scenario,retry_http=retry_http).generate(task,p)
            except (TransportUnknown,TimeoutError,OSError) as error:
                with store._mutex:
                    if isinstance(error,RetryableHTTP):
                        _record_http_retry(store,task,aid,store.get('attempts',aid+'.receipt'))
                    else:
                        store.event('transport_unknown',task_id=task.task_id,attempt_id=aid)
                    transition(overall,task.stage,'running','transport_unknown')
                    failures[0]+=1
                    unavailable=failures[0]>=6
                # 放弃本轮：让其他 worker 跑完，下一轮调度段会重新排到这道题并退避重试。
                if auto_retry_unknown and not unavailable: return None
                return ('transport_unknown',task.task_id,None)
            except ValueError:
                # 调用前凭据/配置失败亦保守记为未决，不打印可能携带请求信息的异常正文。
                with store._mutex:
                    store.event('transport_unknown',task_id=task.task_id,attempt_id=aid,reason='backend rejected configuration')
                    transition(overall,task.stage,'running','transport_unknown')
                raise ValueError('后端配置或凭据检查失败；状态已记录，请检查配置') from None
            with store._mutex:
                parsed=record_result(store,task,result,aid)
                completed[0]+=1
                failures[0]=failures[0]+1 if result.error else 0
                transition(overall,task.stage,'running','succeeded' if parsed['valid'] else 'invalid')
                report('finish',stage=task.stage,item_id=task.context.get('item_id',''),agent=task.context.get('agent',''),state='succeeded' if parsed['valid'] else 'invalid',
                       task_id=task.task_id,item_position=item_order[key]+1)
                if result.error:
                    global_error=failure_policy.global_backend_error(result.error) or failures[0]>=6
                    if not continue_on_error or global_error: return ('backend_error',task.task_id,result.error)
        return None
    def run_plans(plans,states,preflight_failures):
        """worker 抛出的异常必须回到主线程，否则会退化成静默的部分失败。"""
        if len(plans)==1: return [run_item(*plans[0],states,preflight_failures)]
        outcomes=[]; error=None
        with futures.ThreadPoolExecutor(max_workers=len(plans)) as pool:
            submitted=[pool.submit(run_item,key,tasks,states,preflight_failures) for key,tasks in plans]
            for pending in submitted:
                try: outcomes.append(pending.result())
                except BaseException as exc:
                    if error is None: error=exc
                    for other in submitted: other.cancel()
        if error is not None: raise error
        return outcomes
    report('prepare',stage='获取运行锁')
    with store.lock():
        from . import failures as failure_policy
        continue_on_error=failure_policy.enabled(store,continue_on_error)
        if continue_on_error and not store.config.get('complete_matrix'):
            raise ValueError('逐题容错策略仅适用于完整矩阵实验')
        manifest=store.get('','manifest')
        if manifest['config_hash']!=canonical_hash(store.config): raise IntegrityError('配置冻结哈希不匹配')
        report('prepare',stage='已取得运行锁，读取断点索引并恢复未完成响应')
        recover(store,progress=lambda u:report('prepare',stage=(
            '读取断点索引' if u.get('source')=='checkpoint' else '首次建立断点索引：仅回放历史事件')+
            f" {u['loaded']}/{u['total']} 条"))
        store.save_checkpoint()
        if question_batch_size is None:
            previous=next((e.get('question_batch_size') for e in reversed(store.all('events'))
                           if e['kind']=='execution_policy' and e.get('question_batch_size')),None)
            from .protocol import STUDY_V2
            question_batch_size=previous or (10 if store.config.get('complete_matrix') and store.config.get('study_protocol')==STUDY_V2 else None)
        from .pipeline import enable_item_pipeline, item_pipeline_enabled
        if item_pipeline or store.config.get('complete_matrix'): enable_item_pipeline(store)
        pipeline=item_pipeline_enabled(store)
        if single_pass:
            report('prepare',stage='登记单次运行变更并保留已完成题')
            from .protocol import enable_single_pass
            enable_single_pass(store)
        if max_calls!=0:
            store.event('execution_policy',scheduling='item_first_v1',auto_retry_unknown=auto_retry_unknown,concurrency=concurrency,retry_http=retry_http,
                        material_gate='per_item' if pipeline else 'configured',recovery_mode='checkpoint_v1',question_batch_size=question_batch_size,
                        continue_on_error=continue_on_error)
        report('prepare',stage='查找可执行任务')
        last_planning=[0]
        def planning(update):
            now=time.monotonic()
            if now-last_planning[0]>=0.5 or update['item_position']==update['item_total']:
                report('prepare',stage=f"恢复任务图：第{update['item_position']}/{update['item_total']}题 {update['item_id']}")
                last_planning[0]=now
        while max_calls is None or issued[0]<max_calls:
            if graph is None: graph=Planner(store,persist=not pipeline).build(progress=planning)
            else:
                # 只刷新上一轮真正跑过的题：别的题没有 worker 写过，视图不会过期。
                for key in sorted(refreshed,key=lambda k:item_order[k]): graph.refresh_item(key)
            events=store.all('events'); selected=store.selection()
            preflight_failures={e['attempt_id'] for e in events if e['kind']=='transport_unknown'
                                and e.get('reason') in NON_CHARGEABLE_FAILURES}
            states={tid:task_state(store,tid,events,selected) for tid in graph.tasks}
            if continue_on_error:
                adopted=failure_policy.adopt_valid_repairs(store,graph,states)
                if adopted:
                    refreshed=adopted
                    report('prepare',stage=f'已自动采用{len(adopted)}题的有效重试回复')
                    continue
            overall=progress_snapshot(store,graph,states)
            allowed=None
            skipped=failure_policy.skipped_items(store) if continue_on_error else {}
            complete={}
            if question_batch_size or continue_on_error:
                from .matrix import result_matrix
                complete={key:True for key in item_order}
                for row in result_matrix(store,graph):
                    if row['status']!='complete': complete[(row['dataset_id'],row['item_id'])]=False
                ordered=list(item_order)
                size=question_batch_size or len(ordered)
                for offset in range(0,len(ordered),size):
                    batch=ordered[offset:offset+size]
                    if not all(complete[key] or key in skipped for key in batch):
                        allowed=set(batch)
                        overall['items']['batch']={'index':offset//size+1,
                            'total':(len(ordered)+size-1)//size,
                            'start':offset+1,'end':offset+len(batch),'complete':sum(complete[key] for key in batch),
                            'skipped':sum(key in skipped and not complete[key] for key in batch),'size':len(batch)}
                        break
            if continue_on_error:
                fatal=failure_policy.exhausted_global_failure(store,graph,states,allowed,preflight_failures)
                if fatal:
                    report('stop',reason='backend_error',task_id=fatal)
                    return {'executed':completed[0],'stop':'backend_error','task_id':fatal,'reason':'全局服务错误已达尝试上限，保留断点而非跳过题目'}
                queued=failure_policy.queue_invalid_retries(store,graph,states,allowed,complete,preflight_failures)
                if queued:
                    refreshed=queued
                    report('prepare',stage=f'已登记{len(queued)}题的有限格式重试')
                    continue
            ready=[t for t in graph.tasks.values() if states[t.task_id]=='ready']
            unknown=[tid for tid,state in states.items() if state=='transport_unknown']
            if skipped:
                ready=[t for t in ready if item_key(t) not in skipped]
                unknown=[tid for tid in unknown if item_key(graph.tasks[tid]) not in skipped]
            if allowed is not None:
                ready=[t for t in ready if item_key(t) in allowed]
                unknown=[tid for tid in unknown if item_key(graph.tasks[tid]) in allowed]
            if unknown and not auto_retry_unknown:
                report('stop',reason='transport_unknown',task_id=unknown[0])
                return {'executed':completed[0],'stop':'transport_unknown','tasks':unknown}
            retry=[]
            for tid in unknown:
                if chargeable_attempts(store,tid,preflight_failures)<store.config['max_attempts']:
                    retry.append(graph.tasks[tid])
                elif tid not in deferred:
                    deferred.add(tid)
                    store.event('retry_exhausted',task_id=tid,reason='保留未知结果，继续其他可执行任务')
                    report('deferred',task_id=tid)
            ready=retry+ready
            if continue_on_error:
                skipped_now=failure_policy.skip_stalled_items(store,graph,states,allowed,complete,ready)
                if skipped_now:
                    refreshed=set()
                    for key in skipped_now: report('item_skipped',item_id=key[1])
                    continue
            if not ready:
                if unknown:
                    report('stop',reason='transport_unknown')
                    return {'executed':completed[0],'stop':'transport_unknown','tasks':unknown}
                if allowed is not None:
                    report('stop',reason='incomplete_batch')
                    return {'executed':completed[0],'stop':'incomplete_batch','batch':overall['items']['batch']}
                break
            started_ids={e['task_id'] for e in events if e['kind']=='started'}
            started_items={item_key(task) for tid,task in graph.tasks.items() if tid in started_ids}
            # 按题去重后再截断；不去重会把同一道题塞进多个 worker，破坏「一 worker 一题」。
            ranked=sorted({item_key(task) for task in ready},key=lambda key:(key not in started_items,item_order[key]))
            position={item_key(task):index for index,task in enumerate(ready)}
            room=max_calls-issued[0] if max_calls is not None else None
            plans=[]
            for key in ranked[:concurrency]:
                tasks=sorted((t for t in ready if item_key(t)==key),key=lambda t:position[item_key(t)])
                take=tasks if room is None else tasks[:max(0,room)]
                if room is not None: room-=len(take)
                if take: plans.append((key,take))
            if not plans: break
            if pipeline:
                # 启动时只读恢复全部题目的视图；只为本批至多 N 道题落盘下一步。
                # 否则迁移已有 500 题材料时，会在首次调用前为全部题目写入 T1。
                graph.persist=True
                for key,_ in plans: graph.refresh_item(key)
            refreshed={key for key,_ in plans}
            overall['items']['position']=item_order[plans[0][0]]+1
            overall['items']['current']=plans[0][0][1]
            overall['items']['active']=[key[1] for key,_ in plans]
            overall['items']['active_positions']=[item_order[key]+1 for key,_ in plans]
            report('batch',total=sum(len(tasks) for _,tasks in plans))
            for outcome in run_plans(plans,states,preflight_failures):
                if outcome and stop_reason is None: stop_reason=outcome
            if stop_reason:
                # 在飞题已经跑完并落盘，停止原因现在才安全上报。
                reason,task_id,detail=stop_reason
                report('stop',reason=reason,task_id=task_id)
                return {'executed':completed[0],'stop':reason,'task_id':task_id,**({'reason':detail} if detail else {})}
    reason='call_limit' if max_calls is not None and issued[0]>=max_calls else 'no_ready_tasks'
    report('stop',reason=reason)
    return {'executed':completed[0],'stop':reason}

def status(store):
    store._event_cache=None
    graph=Planner(store,persist=False).build(); events=store.all('events'); selected=store.selection()
    task_states={t:task_state(store,t,events,selected) for t in graph.tasks}
    states=Counter(task_states.values())
    budget=Budget(store).totals()
    revision=next((e['sequence'] for e in reversed(events) if e['kind']=='revision'),0)
    progress=progress_snapshot(store,graph,task_states)
    from .failures import skipped_items
    skipped=skipped_items(store)
    retired=({'invalid':progress['invalid'],'superseded_material_invalid':states.get('invalid',0)-progress['invalid']}
             if store.config.get('complete_matrix') else {})
    return {'run_id':store.path.name,'active_tasks':len(graph.tasks),'recorded_tasks':len(store.ids('tasks')),
        'progress':progress,'actionable_ready':sum(state=='ready' and (graph.tasks[tid].context['dataset_id'],graph.tasks[tid].context['item_id']) not in skipped for tid,state in task_states.items()),
        **{k:states.get(k,0) for k in ('ready','succeeded','invalid','transport_unknown')},**retired,'blocked_groups':graph.blocked,
        'materials':dict(Counter(m['status'] for m in graph.materials)),'comparisons':len(graph.comparisons),'budget':budget,
        'inherited_calls':sum(e['kind']=='started' and e['sequence']<revision for e in events),
        'new_calls':sum(e['kind']=='started' and e['sequence']>revision for e in events),
        'real_http_calls':sum(e['kind']=='started' and e.get('provider') not in ('mock','manual') and e['sequence']>revision for e in events)}

def new_attempt(store,task_id,reason):
    if not reason.strip(): raise ValueError('必须填写重答原因')
    with store.lock():
        load_task(store,task_id)
        if task_id not in Planner(store,persist=False).build().tasks: raise ValueError('不能重答已不活动的历史槽位')
        state=task_state(store,task_id)
        if state in ('ready','succeeded'): raise ValueError('该槽位尚未失败或已有有效选定回复')
        starts=store.attempts(task_id)
        if chargeable_attempts(store,task_id)>=store.config['max_attempts']: raise BudgetError('该槽位已达到尝试上限')
        aid=f'{task_id}-a{len(starts)+1:03d}'
        store.event('retry_requested',task_id=task_id,attempt_id=aid,reason=reason)
        return {'attempt_id':aid,'selection':'新结果仍需显式选定，不覆盖旧结果'}

def reparse(store,task_id,version):
    with store.lock():
        task=load_task(store,task_id); attempts=store.attempts(task_id)
        if not attempts: raise ValueError('该槽位还没有原始响应')
        aid=attempts[-1]['attempt_id']; raw=store.read_blob('raw',aid,'.bin'); d=store.get('results',aid)
        result=ModelResult(raw,d['text'],d['returned_model'],d['stop_reason'],d['usage'],d['error'])
        children=[t for t in store.all('tasks') if task_id in t['dependencies']]
        parsed=_save_parse(store,task,result,aid,version,select=not children)
        return {**parsed,'requires_revision':bool(children)}

def submit(store,task_id,raw,attempt_id=None):
    with store.lock():
        graph=Planner(store).build()
        if task_id not in graph.tasks: raise ValueError('只能提交当前活动且依赖就绪的槽位')
        task=graph.tasks[task_id]
        existing=store.attempts(task_id)
        aid=attempt_id or f'{task_id}-a{len(existing)+1:03d}'
        if existing and attempt_id and any(e['attempt_id']==aid for e in existing):
            if store.read_blob('raw',aid,'.bin')!=raw: raise IntegrityError('重复导入内容冲突')
            return {'attempt_id':aid,'idempotent':True}
        if task_state(store,task_id)!='ready': raise ValueError('提交前必须显式创建重答尝试')
        expected=f'{task_id}-a{len(existing)+1:03d}'
        if aid!=expected: raise ValueError('尝试编号不匹配')
        if chargeable_attempts(store,task_id)>=store.config['max_attempts']: raise BudgetError('尝试上限已到')
        reserved=Budget(store).reserve(task,profile(store.config,task.profile_id))
        store.event('started',task_id=task_id,attempt_id=aid,stage=task.stage,provider='manual',reserved_tokens=reserved,wire_format='text')
        return record_result(store,task,ModelResult(raw,raw.decode('utf-8'),None,'manual',None),aid)

def fork_revision(store,task_id,attempt_id,run_id=None,parse_id=None):
    with store.lock():
        store.verify()
        recover(store)
        candidates=[p for p in store.all('parses') if p['task_id']==task_id and p['attempt_id']==attempt_id and p['valid']]
        if parse_id: candidates=[p for p in candidates if canonical_hash(p)==parse_id]
        if len(candidates)!=1: raise ValueError('需要唯一有效候选解析；有多个版本时提供 --select-parse')
        chosen=canonical_hash(candidates[0])
        target=RunStore.create(store.path.parent,run_id or f'{store.path.name}.rev-{chosen[:8]}',store.config,parent_run=str(store.path),parent_manifest_hash=canonical_hash(store.get('','manifest')))
        # 新运行内保留父运行证据副本；不改父文件，不复制锁及 manifest。
        for collection in ('tasks','raw','results','parses','events','snapshots','materials','comparisons','messages','deliveries','inputs','analysis_inputs','attempts'):
            for path in (store.path/collection).iterdir():
                if path.is_file(): shutil.copyfile(path,target.path/collection/path.name)
        target._event_cache=None
        target.event('revision',parent_run=str(store.path),task_id=task_id,attempt_id=attempt_id)
        target.event('selected',task_id=task_id,parse_id=chosen,attempt_id=attempt_id,reason='explicit revision')
        Planner(target).build()
        return target
