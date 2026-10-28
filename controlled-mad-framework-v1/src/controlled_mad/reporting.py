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
    rq2=bundle.get('experiment')=='rq2_five_round'
    text=('# RQ2 五轮补充实验报告\n\n' if rq2 else '# 受控 MAD 运行报告\n\n')
    text+=('本报告包含预定题目层 bootstrap 推断与 Holm 校正；模拟后端输出只能用于工程验收。\n\n'
           if 'inference' in bundle else '本报告为描述性结果；模拟后端输出只能用于工程验收。\n\n')
    text+='```json\n'+json.dumps(den,ensure_ascii=False,indent=2)+'\n```\n\n'
    if bundle.get('matrix_summary'):
        matrix=bundle['matrix_summary']
        text+=f"完整条件验收：{matrix['complete_items']}/{matrix['total_items']}题，{matrix['complete_outputs']}/{matrix['expected_outputs']}个真实终局结果。逐格三票、裁决及回复引用见 result_matrix 导出。\n\n"
        text+=f"位置布局版本：{bundle.get('matrix_layout','slot_first_v1')}。\n\n"
        if bundle.get('failure_summary'):
            failed=bundle['failure_summary']
            text+=f"跳过{failed['skipped_items']}题，保留{failed['missing_outputs']}个缺失终局结果；失败原因、尝试引用及缺格见failures导出。跳过题不计为完整题。\n\n"
        if bundle.get('matrix_reuse'):
            text+=f"兼容回复复用来源：{bundle['matrix_reuse']['source_run_id']}；旧的支持/限制前置回复不复用，新版按目标段整体前置重新审核和讨论。\n\n"
    text+=('五轮 M-R − M-O 配对轨迹、题目聚合、逐题 bootstrap 区间、材料资格及执行记录见 exports 目录。T5 才是终局。\n'
           if rq2 else '完整逐标签效应、干预、O/O 诊断、材料资格及执行记录见 exports 目录。\n')
    if bundle.get('protocol_amendment'):
        text+='协议变更：已完成题保留旧设计；其余题一次重复，I 在 T1 终止，MAD 在 T3 终止。新旧 design 分组分别汇总，不合并为同一实验条件。\n\n'
    final=[row for row in bundle.get('condition_summaries',[]) if row.get('is_final',row['round']==3)]
    if final:
        text+='\n## 终局表现\n\n| 数据/模型 | 设计 | 操作 | 条件 | 完整题/合格题 | 有金标题 | 准确率 |\n|---|---|---|---|---|---|---|\n'
        for row in final:
            accuracy='NA' if row['accuracy'] is None else f"{row['accuracy']:.4f}"
            text+=f"| {row['dataset_id']}/{row['model_id']} | {row.get('design','legacy_three_round')} | {row['operation']} | {row['protocol']}-{row['variant']}-T{row['round']} | {row['complete_items']}/{row['planned_items']} | {row['gold_items']} | {accuracy} |\n"
        text+='\n各题等权；先在题内平均重复；无多数在有金标时算不正确；缺失不作为裁决。\n'
    primary=[row for row in bundle.get('inference',[]) if row['primary']]
    if primary:
        text+='\n## 终局类别向量整体检验\n\n| 数据/模型 | 设计 | 操作 | 独立题数 | p | Holm p |\n|---|---|---|---|---|---|\n'
        for row in primary:
            text+=f"| {row['dataset_id']}/{row['model_id']} | {row.get('design','legacy_three_round')} | {row['operation']} | {row['independent_units']} | {row['p_value']} | {row.get('holm_p_value')} |\n"
        text+='\n区间为逐类别点态区间，非同时区间；推断条件于冻结 S0 和材料，小分母及退化情况见 inference 导出。\n'
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
