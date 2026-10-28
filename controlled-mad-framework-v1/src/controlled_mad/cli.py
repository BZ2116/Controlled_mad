import argparse, json, shutil, sys, threading, unicodedata
from pathlib import Path
from . import api
from .budget import BudgetError
from .store import IntegrityError
from .identity import canonical_bytes, canonical_hash
from .config import load_config

class ProgressReporter:
    """在 stderr 单行刷新固定计划的累计进度，stdout 保留 JSON。"""
    def __init__(self):
        self.tty=sys.stderr.isatty()
        self.active=False
        self.previous_width=0
        self.lock=threading.Lock()

    def display(self,line):
        with self.lock:
            self._display(line)

    def _display(self,line):
        if self.tty:
            limit=max(1,shutil.get_terminal_size((80,24)).columns-1)
            visible=[]; width=0
            for char in line:
                char_width=0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ('F','W') else 1
                if width+char_width>limit: break
                visible.append(char); width+=char_width
            sys.stderr.write('\r'+''.join(visible)+' '*max(0,self.previous_width-width))
            self.previous_width=width
            self.active=True
        else:
            sys.stderr.write(line+'\n')
        sys.stderr.flush()

    def close(self):
        with self.lock:
            if self.active:
                sys.stderr.write('\n')
                self.active=False
            sys.stderr.flush()

    def __call__(self,update):
        event=update['event']
        if event=='item_skipped':
            self.display(f"本题重试/资格流程已耗尽：{update['item_id']}，已记录缺格并跳过，继续本批其他题")
            return
        if event=='retry_wait':
            self.display(f"网络异常，{update['seconds']} 秒后自动重试 | {update['stage']} {update['item_id']} {update['agent']}")
            return
        if event=='deferred':
            self.display('该任务已达重试上限，保留断点并继续其他任务')
            return
        if event=='prepare':
            self.display(f"准备运行：{update['stage']}")
            return
        if event=='verify_progress':
            self.display(f"核验历史文件：{update['verified_objects']} 个 | {update['collection']} | 已用 {update['elapsed']:.1f} 秒")
            return
        if event=='retry':
            self.display(f"断点恢复：重试 {update['stage']} {update['item_id']} {update['agent']}（{update['attempt_id']}）")
            return
        if event=='stop':
            reason={'transport_unknown':'请求结果未知，已保存断点','backend_error':'服务返回错误，已保存断点',
                    'call_limit':'达到本次调用上限','no_ready_tasks':'当前无可执行任务',
                    'incomplete_batch':'本批结果未齐，已保存断点，后续批次尚未启动'}.get(update['reason'],update['reason'])
            if update.get('overall'):
                self.show_overall(update,f" | 停止：{reason}")
            else:
                self.display(f"运行停止：{reason}；本次完成 {update['completed']} 次请求")
            self.close()
            return
        if update.get('overall'):
            detail=''
            if event in ('start','finish'):
                detail=f" | {update['stage']} {update['agent']}"
                detail+=' 请求中' if event=='start' else (' 完成' if update['state']=='succeeded' else ' 无效')
            self.show_overall(update,detail)
            return
        total=update['total']
        done=update.get('index',0)
        filled=20*done//total if total else 20
        bar='█'*filled+'░'*(20-filled)
        detail=''
        if event in ('start','finish'):
            detail=f" | {update['stage']} {update['item_id']} {update['agent']}"
            detail+=' 请求中' if event=='start' else (' 完成' if update['state']=='succeeded' else ' 无效')
        line=f"进度 [{bar}] {done}/{total} | 本次 {update['completed']}{detail}"
        self.display(line)

    def show_overall(self,update,detail):
        counts=update['overall']; items=counts['items']
        resilient='processed' in items
        total=items['total'] if resilient else counts['planned']
        done=items['processed'] if resilient else counts['settled']
        filled=10*done//total if total else 10
        bar='█'*filled+'░'*(10-filled)
        percent=min(99.9,100*done/total) if total and done<total else 100
        batch=items.get('batch')
        if batch: detail=f" | 批次 {batch['index']}/{batch['total']}（第{batch['start']}–{batch['end']}题，{batch['complete']}/{batch['size']}完成，跳过{batch.get('skipped',0)}）"+detail
        active=items.get('active') or []
        position=update.get('item_position') or items.get('position')
        if not position: current='待选题'
        else: current=f"第{position}/{items['total']}题"
        positions=items.get('active_positions') or []
        parallel=f" | 并行题号 {','.join(map(str,positions))}" if positions else ''
        line=(f"题目 {items['complete']}/{items['total']}完成 | {current}{detail}{parallel}"
              f" | {'处理进度' if resilient else '整体'}[{bar}] {percent:.1f}% {done}/{total}"
              f" | 成 {counts['succeeded']} 取 {counts['cancelled']}"
              f" 待 {counts['ready']} 阻 {counts['waiting']} 异 {counts['invalid']+counts['transport_unknown']}"
              f" | 本次请求 {update['completed']}")
        if 'expected_results' in items:
            line=line.replace('完成 |','完成 | 结果 '+str(items['complete_results'])+'/'+str(items['expected_results'])+' |',1)
        if 'skipped' in items:
            line=line.replace('完成 |',f"完成 跳过{items['skipped']} 已处理{items['processed']}/{items['total']} |",1)
        self.display(line)

def add_execution_flags(q):
    """执行策略不是实验协议：不进冻结配置，只写进 execution_policy 事件，老运行照读。"""
    q.add_argument('--concurrency',type=int,default=1,help='同时推进的题目数；只按题并发，题内仍严格串行')
    q.add_argument('--retry-http',action='store_true',help='把 408/429/5xx 交给退避重试；默认关闭以保住「HTTP 失败必须停机」的协议不变量')

def parser():
    p=argparse.ArgumentParser(description='受控 MAD 完整实验流程；默认离线，不会自动读取 .env')
    sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('replication-plan',help='校验已启用复核配置，保留待定数据/模型槽位')
    q.add_argument('--matrix',required=True)
    q=sub.add_parser('main-run',help='严格 v10 主实验；支持保留记录的逐题流水线和断点续跑')
    q.add_argument('--config',required=True); q.add_argument('--run-id',required=True); q.add_argument('--runs-root')
    q.add_argument('--credentials-file',help='只读取用户明确指定的本地凭据文件；不写密钥')
    q.add_argument('--prepare-only',action='store_true')
    q.add_argument('--concurrency',type=int,default=3)
    q.add_argument('--item-pipeline',action='store_true',help='追加逐题调度变更：每题材料冻结后立即讨论；此运行后续续跑自动沿用')
    q.add_argument('--single-pass',action='store_true',help='追加协议变更：保留已完成题，其余题只跑一次；I 仅一轮，MAD 三轮，续跑自动沿用')
    q.add_argument('--reuse-matrix-source',help='离线继承兼容回复：位置规则v1→v2，或v2完整题目子集扩展；续跑可保留')
    q.add_argument('--question-batch-size',type=int,help='每批题数；本批每题16格齐全后才进入下一批')
    q.add_argument('--continue-on-error',action='store_true',help='单题无效回复有限重试，成功自动采用；耗尽则记录跳过，续跑自动沿用')
    q=sub.add_parser('prepare-matrix-v2',help='无API准备v2位置条件，复用v1中兼容的原始回复')
    q.add_argument('--config',required=True); q.add_argument('--source-run-id',required=True)
    q.add_argument('--run-id',required=True); q.add_argument('--runs-root',required=True)
    q.add_argument('--preview',action='store_true',help='只读计算复用数量，不创建运行')
    for name in ('validate','plan','init'):
        q=sub.add_parser(name,help={'validate':'校验配置和数据','plan':'计算逻辑比较与物理任务上界','init':'冻结配置、输入和提示'}[name])
        q.add_argument('--config',required=True)
        if name=='init':
            q.add_argument('--run-id',required=True); q.add_argument('--runs-root')
    q=sub.add_parser('rq2',help='五轮补充实验：首次冻结，之后按同一配置自动续跑')
    q.add_argument('--config',required=True); q.add_argument('--run-id',required=True); q.add_argument('--runs-root')
    q.add_argument('--allow-network',action='store_true'); q.add_argument('--max-calls',type=int); q.add_argument('--backend',choices=('mock','native'))
    add_execution_flags(q)
    for name in ('advance','export-tasks','import-responses','run','status','matrix-status','failure-status','resume','reparse','retry','fork-revision','analyze','trace','verify','freeze-materials','coding-export','coding-import','coding-status','material-validation-export','material-validation-import','material-validation-status'):
        q=sub.add_parser(name)
        q.add_argument('--run-id',required=True); q.add_argument('--runs-root')
        if name in ('run','resume'):
            q.add_argument('--allow-network',action='store_true'); q.add_argument('--max-calls',type=int); q.add_argument('--backend',choices=('mock','native'))
            add_execution_flags(q)
        if name=='export-tasks': q.add_argument('--stage',choices=('S0','generate','annotate','audit','T1','T2','T3','T4','T5'))
        if name=='import-responses': q.add_argument('--source',required=True)
        if name in ('coding-import','material-validation-import'): q.add_argument('--source',required=True)
        if name in ('reparse','retry','fork-revision'): q.add_argument('--task-id',required=True)
        if name=='reparse': q.add_argument('--parser-version',required=True,choices=('vote-v1','vote-v2'))
        if name=='retry': q.add_argument('--reason',required=True)
        if name=='fork-revision':
            q.add_argument('--select-attempt',required=True); q.add_argument('--select-parse'); q.add_argument('--new-run-id')
        if name=='trace': q.add_argument('--item-id',required=True)
    return p

def dispatch(a):
    if a.command=='prepare-matrix-v2':
        from .matrix_revision import prepare_revision
        return prepare_revision(a.config,a.source_run_id,a.run_id,a.runs_root,a.preview)
    if a.command=='main-run':
        from .runner import run_main
        reporter=ProgressReporter()
        try: return run_main(a.config,a.run_id,a.runs_root,a.credentials_file,a.prepare_only,a.concurrency,reporter,a.item_pipeline,a.single_pass,a.reuse_matrix_source,a.question_batch_size,a.continue_on_error)
        finally: reporter.close()
    if a.command=='replication-plan':
        from .replication import replication_plan
        return replication_plan(a.matrix)
    if a.command=='validate': return api.validate(a.config)
    if a.command=='plan': return api.plan(a.config)
    if a.command=='init':
        store=api.create_run(a.config,a.run_id,a.runs_root)
        return {'run_id':store.path.name,'path':str(store.path),'status':api.status(store)}
    if a.command=='rq2':
        if a.max_calls is not None and a.max_calls<0: raise ValueError('--max-calls 不得为负')
        config=load_config(a.config)
        if config.get('experiment')!='rq2_five_round': raise ValueError('rq2 命令只能运行五轮补充实验配置')
        root=Path(a.runs_root) if a.runs_root else Path.cwd()/'runs'
        run_path=root/a.run_id
        if run_path.exists():
            store=api.open_run(a.run_id,a.runs_root)
            if store.config.get('experiment')!='rq2_five_round' or canonical_hash(store.config)!=canonical_hash(config):
                raise ValueError('现有运行的实验模式或冻结配置与本次输入不同')
        else: store=api.create_run(a.config,a.run_id,a.runs_root)
        reporter=ProgressReporter()
        try:
            execution=api.execute_ready(store,a.allow_network,a.max_calls,'mock' if a.backend=='mock' else None,progress=reporter,auto_retry_unknown=True,concurrency=a.concurrency,retry_http=a.retry_http)
        finally: reporter.close()
        return {'execution':execution,'status':api.status(store)}
    store=api.open_run(a.run_id,a.runs_root)
    if a.command=='status': return api.status(store)
    if a.command=='verify': return api.verify_run(store)
    if a.command=='freeze-materials': return api.freeze_materials(store)
    if a.command=='coding-export': return api.export_coding(store)
    if a.command=='coding-import': return api.import_coding(store,a.source)
    if a.command=='coding-status': return api.coding_status(store)
    if a.command=='material-validation-export': return api.export_material_validation(store)
    if a.command=='material-validation-import': return api.import_material_validation(store,a.source)
    if a.command=='material-validation-status': return api.material_validation_status(store)
    if a.command=='advance': return {'active_tasks':len(api.advance(store))}
    if a.command=='export-tasks': return api.export_tasks(store,a.stage)
    if a.command in ('run','resume'):
        if a.max_calls is not None and a.max_calls<0: raise ValueError('--max-calls 不得为负')
        reporter=ProgressReporter()
        try:
            execution=api.execute_ready(store,a.allow_network,a.max_calls,'mock' if a.backend=='mock' else None,progress=reporter,auto_retry_unknown=True,concurrency=a.concurrency,retry_http=a.retry_http)
        finally:
            reporter.close()
        return {'execution':execution,'status':api.status(store)}
    if a.command=='reparse': return api.reparse(store,a.task_id,a.parser_version)
    if a.command=='retry': return api.new_attempt(store,a.task_id,a.reason)
    if a.command=='fork-revision':
        revised=api.fork_revision(store,a.task_id,a.select_attempt,a.new_run_id,a.select_parse)
        return {'run_id':revised.path.name,'status':api.status(revised)}
    if a.command=='matrix-status': return api.matrix_status(store)
    if a.command=='failure-status':
        from .failures import failure_status
        return failure_status(store)
    if a.command=='analyze': return api.analyze_and_export(store)
    if a.command=='trace': return {'path':api.export_trace(store,a.item_id)}
    if a.command=='import-responses':
        rows=[json.loads(line) for line in Path(a.source).read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        # 先检查整批格式，避免坏行使已导入部分难以追踪；逐条提交仍独占持久化。
        if any(set(row)!={'task_id','attempt_id','text'} or not all(isinstance(v,str) for v in row.values()) for row in rows):
            raise ValueError('每行必须恰有字符串 task_id、attempt_id 和 text')
        return {'imported':[api.submit(store,row['task_id'],row['text'].encode('utf-8'),row['attempt_id']) for row in rows]}
    raise ValueError('未知命令')

def main(argv=None):
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):
            try: stream.reconfigure(encoding='utf-8',errors='replace')
            except (ValueError,OSError): pass
    args=parser().parse_args(argv)
    try:
        result=dispatch(args)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.command=='main-run' and result['phase'] not in ('prepared','completed','completed_with_failures'): return 3
        if args.command in ('run','resume','rq2'):
            if result['execution']['stop'] in ('transport_unknown','backend_error') or result['status']['invalid'] or result['status']['transport_unknown']: return 3
        return 0
    except BudgetError as e:
        print(json.dumps({'错误':str(e)},ensure_ascii=False),file=sys.stderr); return 4
    except IntegrityError as e:
        print(json.dumps({'完整性错误':str(e)},ensure_ascii=False),file=sys.stderr); return 5
    except (ValueError,KeyError,TypeError,FileNotFoundError,FileExistsError) as e:
        print(json.dumps({'输入错误':str(e)},ensure_ascii=False),file=sys.stderr); return 2
