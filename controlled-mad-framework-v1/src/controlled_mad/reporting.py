import csv, io, json, html
from .identity import canonical_hash, canonical_bytes
from .scheduler import Planner

def export_analysis(bundle,store):
    aid=canonical_hash(bundle)
    paths={}
    for key,value in bundle.items():
        if isinstance(value,list):
            name=f'{key}-{aid[:16]}'
            store.write_blob('exports',name,b''.join(canonical_bytes(row)+b'\n' for row in value),'.jsonl')
            paths[key]=str(store.path/'exports'/f'{name}.jsonl')
            if value:
                columns=sorted({k for row in value for k in row})
                out=io.StringIO(); writer=csv.DictWriter(out,fieldnames=columns); writer.writeheader()
                for row in value:
                    writer.writerow({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()})
                store.write_blob('exports',name,out.getvalue().encode('utf-8'),'.csv')
    den=bundle['denominators']
    text='# 受控 MAD 运行报告\n\n本报告为描述性结果，不包含显著性检验。模拟后端输出只能用于工程验收。\n\n'
    text+='```json\n'+json.dumps(den,ensure_ascii=False,indent=2)+'\n```\n\n'
    text+='完整逐标签效应、干预、O/O 诊断、材料资格及执行记录见 exports 目录。\n'
    store.write_blob('reports',f'report-{aid[:16]}',text.encode('utf-8'),'.md')
    store.put('reports',f'analysis-{aid[:16]}',bundle)
    return {'analysis_id':aid,'report':str(store.path/'reports'/f'report-{aid[:16]}.md'),'exports':paths}

def trace(store,item_id):
    graph=Planner(store,persist=False).build()
    tasks=[t for t in graph.tasks.values() if t.context['item_id']==item_id]
    if not tasks: raise ValueError('没有该题的活动任务')
    sections=[]
    for task in tasks:
        sections.append('<details><summary>'+html.escape(f'{task.stage} {task.context.get("variant","")} {task.context.get("agent","")} {task.task_id[:12]}')+'</summary><pre>'+html.escape(json.dumps({'context':task.context,'messages':task.messages},ensure_ascii=False,indent=2))+'</pre></details>')
    body='<!doctype html><meta charset="utf-8"><title>MAD 消息审计</title><h1>'+html.escape(item_id)+'</h1>'+''.join(sections)
    name='trace-'+canonical_hash(body)[:20]
    store.write_blob('reports',name,body.encode('utf-8'),'.html')
    return str(store.path/'reports'/f'{name}.html')
