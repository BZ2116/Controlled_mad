"""所有阶段共用的执行、提交和恢复入口。默认不连接真实服务。"""
import json, os, shutil, time
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
from .backends.transport import TransportUnknown
from .progress import progress_snapshot, transition

ROOT=Path(__file__).resolve().parents[2]

def create_run(config_path,run_id,root=None):
    c=load_config(config_path)
    adapter=adapter_for(c['dataset']['adapter'])
    bundle=adapter.load(c['dataset']['source'])
    subset_record=None
    if c.get('experiment')=='rq2_five_round': bundle,subset_record=select_bundle(bundle,c['subset'])
    if not bundle.items: raise ValueError('数据集中没有可运行题目，请检查拒绝分母')
    names=('s0','debate','rhetoric','audit','annotate')
    overrides=c.get('prompts',{})
    if set(overrides)-set(names): raise ValueError('未知提示名称')
    templates={name:(Path(config_path).resolve().parent/overrides[name]).read_text(encoding='utf-8-sig') if name in overrides else files('controlled_mad').joinpath('resources',f'{name}.v1.txt').read_text(encoding='utf-8') for name in names}
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
        store.write_blob('inputs','items',b''.join(canonical_bytes(i)+b'\n' for i in bundle.items),'.jsonl')
        store.write_blob('analysis_inputs','gold',b''.join(canonical_bytes(g)+b'\n' for g in bundle.gold),'.jsonl')
        store.event('initialized',source_hash=bundle.source_hash)
        Planner(store).build()
    return store

def backend_for(store,task,on_raw=None):
    provider=profile(store.config,task.profile_id).provider
    if provider=='mock': return MockBackend(store.get('inputs','mock'),on_raw)
    return OpenAIBackend(on_raw=on_raw) if provider=='openai' else AnthropicBackend(on_raw=on_raw)

def load_task(store,task_id):
    t=store.get('tasks',task_id)
    return Task(t['task_id'],t['stage'],t['profile_id'],tuple(t['messages']),tuple(t['dependencies']),t['context'],t['prompt_hash'])

def parse_result(task,result,version):
    if result.error: return {'valid':False,'value':None,'error':result.error}
    try:
        if task.stage in ('S0','T1','T2','T3','T4','T5','generate'):
            return parse_vote(result.text,tuple(task.context['labels']),version)
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
                            and e.get('reason')=='backend rejected configuration'}
    return sum(a['attempt_id'] not in preflight_failures for a in store.attempts(task_id))

def check_credential(p):
    if p.provider!='mock' and not os.environ.get(p.credential_env):
        raise ValueError(f'凭据环境变量未设置：{p.credential_env}；请先在当前终端加载密钥，再续跑')

def recover(store):
    recovered=[]; events=store.all('events'); selected=store.selection()
    finished={e['attempt_id'] for e in events if e['kind']=='finished'}
    for start in (e for e in events if e['kind']=='started' and e['attempt_id'] not in finished):
        aid=start['attempt_id']; task=load_task(store,start['task_id'])
        rawfile=store.path/'raw'/f'{aid}.bin'
        if not rawfile.exists(): continue
        raw=store.read_blob('raw',aid,'.bin')
        if (store.path/'results'/f'{aid}.json').exists():
            d=store.get('results',aid); result=ModelResult(raw,d['text'],d['returned_model'],d['stop_reason'],d['usage'],d['error'])
        else:
            if start.get('wire_format')=='text': result=ModelResult(raw,raw.decode('utf-8'),None,'manual',None)
            else:
                backend=backend_for(store,task); p=profile(store.config,task.profile_id)
                receiptfile=store.path/'attempts'/f'{aid}.receipt.json'
                if receiptfile.exists() and p.provider!='mock':
                    receipt=store.get('attempts',aid+'.receipt')
                    result=backend.decode_received(raw,p,receipt['status'],receipt['headers'])
                else: result=backend.decode(raw,p)
        record_result(store,task,result,aid)
        recovered.append(aid)
    return recovered

def execute_ready(store,allow_network=False,max_calls=None,backend_override=None,progress=None,auto_retry_unknown=False):
    completed=0
    issued=0
    consecutive_failures=0
    deferred=set()
    overall=None
    graph=None
    current_item=None
    item_key=lambda task:(task.context['dataset_id'],task.context['item_id'])
    item_order={(row['dataset_id'],row['item_id']):index for index,row in enumerate(store.get('inputs','items')['items'])}
    def report(event,**details):
        if progress is not None: progress({'event':event,'completed':completed,'overall':overall,**details})
    report('prepare',stage='等待运行锁并核验记录')
    with store.lock():
        store.verify()
        report('prepare',stage='恢复已收到的响应')
        recover(store)
        if max_calls!=0:
            store.event('execution_policy',scheduling='item_first_v1',auto_retry_unknown=auto_retry_unknown)
        report('prepare',stage='查找可执行任务')
        while max_calls is None or issued<max_calls:
            graph=Planner(store).build() if graph is None else graph.refresh_item(current_item)
            events=store.all('events'); selected=store.selection()
            preflight_failures={e['attempt_id'] for e in events if e['kind']=='transport_unknown'
                                and e.get('reason')=='backend rejected configuration'}
            states={tid:task_state(store,tid,events,selected) for tid in graph.tasks}
            overall=progress_snapshot(store,graph,states)
            ready=[t for t in graph.tasks.values() if states[t.task_id]=='ready']
            unknown=[tid for tid,state in states.items() if state=='transport_unknown']
            if unknown and not auto_retry_unknown:
                report('stop',reason='transport_unknown',task_id=unknown[0])
                return {'executed':completed,'stop':'transport_unknown','tasks':unknown}
            retry=[]
            for tid in unknown:
                if chargeable_attempts(store,tid,preflight_failures)<store.config['max_attempts']:
                    retry.append(graph.tasks[tid])
                elif tid not in deferred:
                    deferred.add(tid)
                    store.event('retry_exhausted',task_id=tid,reason='保留未知结果，继续其他可执行任务')
                    report('deferred',task_id=tid)
            ready=retry+ready
            if not ready:
                if unknown:
                    report('stop',reason='transport_unknown')
                    return {'executed':completed,'stop':'transport_unknown','tasks':unknown}
                break
            started_ids={e['task_id'] for e in events if e['kind']=='started'}
            started_items={item_key(task) for tid,task in graph.tasks.items() if tid in started_ids}
            current_item=min({item_key(task) for task in ready},key=lambda key:(key not in started_items,item_order[key]))
            overall['items']['current']=current_item[1]
            overall['items']['position']=item_order[current_item]+1
            item_ready=[task for task in ready if item_key(task)==current_item]
            batch=item_ready[:max_calls-issued] if max_calls is not None else item_ready
            report('batch',total=len(batch))
            for batch_index,task in enumerate(batch):
                p=profile(store.config,task.profile_id)
                if backend_override=='mock' and p.provider!='mock': raise ValueError('不能用 mock 替换冻结的真实后端')
                if p.provider!='mock' and not (allow_network and store.config['network_enabled']): raise BudgetError('真实调用需要冻结网络开关及 --allow-network')
                check_credential(p)
                if states[task.task_id]=='transport_unknown':
                    count=chargeable_attempts(store,task.task_id,preflight_failures)
                    delay=min(30,5*2**max(0,count-1))
                    report('retry_wait',seconds=delay,stage=task.stage,item_id=task.context.get('item_id',''),agent=task.context.get('agent',''))
                    time.sleep(delay)
                    attempt_id=f'{task.task_id}-a{len(store.attempts(task.task_id))+1:03d}'
                    store.event('retry_requested',task_id=task.task_id,attempt_id=attempt_id,reason='传输异常自动退避重试')
                    transition(overall,task.stage,'transport_unknown','ready')
                reserved=Budget(store).reserve(task,p)
                starts=store.attempts(task.task_id)
                if chargeable_attempts(store,task.task_id,preflight_failures)>=store.config['max_attempts']: raise BudgetError('该槽位已达到尝试上限')
                aid=f'{task.task_id}-a{len(starts)+1:03d}'
                store.event('started',task_id=task.task_id,attempt_id=aid,stage=task.stage,provider=p.provider,reserved_tokens=reserved,wire_format=p.provider)
                issued+=1
                transition(overall,task.stage,'ready','running')
                report('start',index=batch_index,total=len(batch),stage=task.stage,item_id=task.context.get('item_id',''),agent=task.context.get('agent',''))
                def commit_received(raw,http_status,headers):
                    store.put('attempts',aid+'.receipt',{'status':http_status,'headers':headers})
                    store.write_blob('raw',aid,raw,'.bin')
                try: result=backend_for(store,task,on_raw=commit_received).generate(task,p)
                except (TransportUnknown,TimeoutError,OSError):
                    store.event('transport_unknown',task_id=task.task_id,attempt_id=aid)
                    transition(overall,task.stage,'running','transport_unknown')
                    consecutive_failures+=1
                    if auto_retry_unknown and consecutive_failures<6:
                        break
                    report('stop',reason='transport_unknown',task_id=task.task_id)
                    return {'executed':completed,'stop':'transport_unknown','task_id':task.task_id}
                except ValueError:
                    # 调用前凭据/配置失败亦保守记为未决，不打印可能携带请求信息的异常正文。
                    store.event('transport_unknown',task_id=task.task_id,attempt_id=aid,reason='backend rejected configuration')
                    transition(overall,task.stage,'running','transport_unknown')
                    raise ValueError('后端配置或凭据检查失败；状态已记录，请检查配置') from None
                parsed=record_result(store,task,result,aid); completed+=1
                consecutive_failures=0
                transition(overall,task.stage,'running','succeeded' if parsed['valid'] else 'invalid')
                report('finish',index=batch_index+1,total=len(batch),stage=task.stage,item_id=task.context.get('item_id',''),agent=task.context.get('agent',''),state='succeeded' if parsed['valid'] else 'invalid')
                if result.error:
                    report('stop',reason='backend_error',task_id=task.task_id)
                    return {'executed':completed,'stop':'backend_error','task_id':task.task_id,'reason':result.error}
    reason='call_limit' if max_calls is not None and issued>=max_calls else 'no_ready_tasks'
    report('stop',reason=reason)
    return {'executed':completed,'stop':reason}

def status(store):
    store._event_cache=None
    graph=Planner(store,persist=False).build(); events=store.all('events'); selected=store.selection()
    task_states={t:task_state(store,t,events,selected) for t in graph.tasks}
    states=Counter(task_states.values())
    budget=Budget(store).totals()
    revision=next((e['sequence'] for e in reversed(events) if e['kind']=='revision'),0)
    return {'run_id':store.path.name,'active_tasks':len(graph.tasks),'recorded_tasks':len(store.ids('tasks')),
        'progress':progress_snapshot(store,graph,task_states),
        **{k:states.get(k,0) for k in ('ready','succeeded','invalid','transport_unknown')},'blocked_groups':graph.blocked,
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
