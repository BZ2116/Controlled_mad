import argparse, json, shutil, sys, unicodedata
from pathlib import Path
from . import api
from .budget import BudgetError
from .store import IntegrityError
from .identity import canonical_bytes

class ProgressReporter:
    """在 stderr 单行刷新固定计划的累计进度，stdout 保留 JSON。"""
    def __init__(self):
        self.tty=sys.stderr.isatty()
        self.active=False

    def display(self,line):
        if self.tty:
            limit=max(1,shutil.get_terminal_size((80,24)).columns-1)
            visible=[]; width=0
            for char in line:
                char_width=0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in ('F','W') else 1
                if width+char_width>limit: break
                visible.append(char); width+=char_width
            sys.stderr.write('\r\033[2K'+''.join(visible))
            self.active=True
        else:
            sys.stderr.write(line+'\n')
        sys.stderr.flush()

    def close(self):
        if self.active:
            sys.stderr.write('\n')
            self.active=False
        sys.stderr.flush()

    def __call__(self,update):
        event=update['event']
        if event=='retry_wait':
            self.display(f"网络异常，{update['seconds']} 秒后自动重试 | {update['stage']} {update['item_id']} {update['agent']}")
            return
        if event=='deferred':
            self.display('该任务已达重试上限，保留断点并继续其他任务')
            return
        if event=='prepare':
            self.display(f"准备运行：{update['stage']}；本次完成 {update['completed']} 次请求")
            return
        if event=='retry':
            self.display(f"断点恢复：重试 {update['stage']} {update['item_id']} {update['agent']}（{update['attempt_id']}）")
            return
        if event=='stop':
            reason={'transport_unknown':'请求结果未知，已保存断点','backend_error':'服务返回错误，已保存断点',
                    'call_limit':'达到本次调用上限','no_ready_tasks':'当前无可执行任务'}.get(update['reason'],update['reason'])
            if update.get('overall'):
                self.show_overall(update,f" | 停止：{reason}")
            else:
                self.display(f"运行停止：{reason}；本次完成 {update['completed']} 次请求")
            self.close()
            return
        if update.get('overall'):
            detail=''
            if event in ('start','finish'):
                detail=f" | {update['stage']} {update['item_id']} {update['agent']}"
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
        counts=update['overall']; total=counts['planned']; done=counts['settled']
        filled=10*done//total if total else 10
        bar='█'*filled+'░'*(10-filled)
        percent=min(99.9,100*done/total) if total and done<total else 100
        items=counts['items']
        current=f"当前 {items['current']} 第{items['position']}题" if items['current'] else '待选题'
        line=(f"整体[{bar}] {percent:.1f}% {done}/{total}"
              f" | 题 {items['complete']}/{items['total']} {current}"
              f" | 成 {counts['succeeded']} 取 {counts['cancelled']}"
              f" 待 {counts['ready']} 阻 {counts['waiting']} 异 {counts['invalid']+counts['transport_unknown']}"
              f" | 本次 {update['completed']}{detail}")
        self.display(line)

def parser():
    p=argparse.ArgumentParser(description='受控 MAD 完整实验流程；默认离线，不会自动读取 .env')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ('validate','plan','init'):
        q=sub.add_parser(name,help={'validate':'校验配置和数据','plan':'计算逻辑比较与物理任务上界','init':'冻结配置、输入和提示'}[name])
        q.add_argument('--config',required=True)
        if name=='init':
            q.add_argument('--run-id',required=True); q.add_argument('--runs-root')
    for name in ('advance','export-tasks','import-responses','run','status','resume','reparse','retry','fork-revision','analyze','trace','verify'):
        q=sub.add_parser(name)
        q.add_argument('--run-id',required=True); q.add_argument('--runs-root')
        if name in ('run','resume'):
            q.add_argument('--allow-network',action='store_true'); q.add_argument('--max-calls',type=int); q.add_argument('--backend',choices=('mock','native'))
        if name=='export-tasks': q.add_argument('--stage',choices=('S0','generate','annotate','audit','T1','T2','T3'))
        if name=='import-responses': q.add_argument('--source',required=True)
        if name in ('reparse','retry','fork-revision'): q.add_argument('--task-id',required=True)
        if name=='reparse': q.add_argument('--parser-version',required=True,choices=('vote-v1','vote-v2'))
        if name=='retry': q.add_argument('--reason',required=True)
        if name=='fork-revision':
            q.add_argument('--select-attempt',required=True); q.add_argument('--select-parse'); q.add_argument('--new-run-id')
        if name=='trace': q.add_argument('--item-id',required=True)
    return p

def dispatch(a):
    if a.command=='validate': return api.validate(a.config)
    if a.command=='plan': return api.plan(a.config)
    if a.command=='init':
        store=api.create_run(a.config,a.run_id,a.runs_root)
        return {'run_id':store.path.name,'path':str(store.path),'status':api.status(store)}
    store=api.open_run(a.run_id,a.runs_root)
    if a.command=='status': return api.status(store)
    if a.command=='verify': return api.verify_run(store)
    if a.command=='advance': return {'active_tasks':len(api.advance(store))}
    if a.command=='export-tasks': return api.export_tasks(store,a.stage)
    if a.command in ('run','resume'):
        if a.max_calls is not None and a.max_calls<0: raise ValueError('--max-calls 不得为负')
        reporter=ProgressReporter()
        try:
            execution=api.execute_ready(store,a.allow_network,a.max_calls,'mock' if a.backend=='mock' else None,progress=reporter,auto_retry_unknown=True)
        finally:
            reporter.close()
        return {'execution':execution,'status':api.status(store)}
    if a.command=='reparse': return api.reparse(store,a.task_id,a.parser_version)
    if a.command=='retry': return api.new_attempt(store,a.task_id,a.reason)
    if a.command=='fork-revision':
        revised=api.fork_revision(store,a.task_id,a.select_attempt,a.new_run_id,a.select_parse)
        return {'run_id':revised.path.name,'status':api.status(revised)}
    if a.command=='analyze': return api.analyze_and_export(store)
    if a.command=='trace': return {'path':api.export_trace(store,a.item_id)}
    if a.command=='import-responses':
        rows=[json.loads(line) for line in Path(a.source).read_text().splitlines() if line.strip()]
        # 先检查整批格式，避免坏行使已导入部分难以追踪；逐条提交仍独占持久化。
        if any(set(row)!={'task_id','attempt_id','text'} or not all(isinstance(v,str) for v in row.values()) for row in rows):
            raise ValueError('每行必须恰有字符串 task_id、attempt_id 和 text')
        return {'imported':[api.submit(store,row['task_id'],row['text'].encode('utf-8'),row['attempt_id']) for row in rows]}
    raise ValueError('未知命令')

def main(argv=None):
    args=parser().parse_args(argv)
    try:
        result=dispatch(args)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if args.command in ('run','resume'):
            if result['execution']['stop'] in ('transport_unknown','backend_error') or result['status']['invalid'] or result['status']['transport_unknown']: return 3
        return 0
    except BudgetError as e:
        print(json.dumps({'错误':str(e)},ensure_ascii=False),file=sys.stderr); return 4
    except IntegrityError as e:
        print(json.dumps({'完整性错误':str(e)},ensure_ascii=False),file=sys.stderr); return 5
    except (ValueError,KeyError,TypeError,FileNotFoundError,FileExistsError) as e:
        print(json.dumps({'输入错误':str(e)},ensure_ascii=False),file=sys.stderr); return 2
