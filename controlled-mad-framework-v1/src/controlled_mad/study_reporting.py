"""Public v2 report; historical renderers and content identities remain intact."""
import json


def value(x):
    return 'NA' if x is None else (f'{x:.5g}' if isinstance(x,float) else str(x))


def render(bundle):
    providers=bundle.get('execution_provenance',{}).get('providers',[])
    text='# 持续同伴互动与表达效应：v2 报告\n\n'
    text+='调用后端：'+(', '.join(providers) if providers else '尚无调用记录')+'。'
    text+=('包含模拟调用，只能用于工程验收。\n\n' if 'mock' in providers else '后端类型不是权重版本证明；返回模型与原始响应另见运行记录。\n\n')
    text+='主问题：共同首次接触之后，持续同伴更新是否改变表达操纵对判断的影响。D 是交互效应，不是中介效应。T1 的 D 为构造零；接受放大、衰减、反转或无可辨识变化，不把不显著当成等效。\n\n'
    text+='## 完整度与分母\n\n```json\n'+json.dumps(bundle['denominators'],ensure_ascii=False,indent=2)+'\n```\n\n'
    if bundle.get('matrix_summary'): text+='独立矩阵验收：`'+json.dumps(bundle['matrix_summary'],ensure_ascii=False)+'`\n\n'
    text+='材料覆盖（含 S0 未齐、资格失败和未构造项）：\n\n| 模型 | 操作 | 计划题数 | 已构造 | 未构造 | 资格状态 |\n|---|---|---|---|---|---|\n'
    for r in bundle['material_coverage']:
        text+=f"| {r['model_id']} | {r['operation']} | {r['planned_items']} | {r['constructed_items']} | {r['unconstructed_items']} | {json.dumps(r['statuses'],ensure_ascii=False)} |\n"
    text+='\n按金标的材料资格分布见 eligibility_by_gold；不得只报合格题而隐去原始计划分母。\n'
    text+='\n## 逐轮表达效应 R−O\n\n| 模型 | 操作 | 条件 | 轮次 | 题数 | vote 向量 | verdict 向量 |\n|---|---|---|---|---|---|---|\n'
    for r in bundle['expression_effects']:
        text+=f"| {r['model_id']} | {r['operation']} | {r['protocol']} | {r['round']} | {r['items']} | {json.dumps(r['E']['vote'])} | {json.dumps(r['E']['verdict'])} |\n"
    text+='\n## D 点估计、点态区间与检验\n\n题目层重采样，三票不拆开；同题共用 O。区间非同时区间；推断条件于冻结初态与材料。退化、单题、缺失保留估计但不输出有效 p 或零宽推断区间。不可估计检验仍占预定 Holm 家族槽位。\n\n'
    text+='| 模型 | 操作 | T | 层 | 标签 | D | 点态区间 | n | 状态 | 向量 p | Holm p | 家族/可估计 |\n|---|---|---|---|---|---|---|---|---|---|---|\n'
    if bundle.get('inference'):
        for r in bundle['inference']:
            for label,estimate in (r['estimate'] or {'NA':None}).items():
                interval=(r['confidence_intervals'] or {}).get(label)
                text+=f"| {r['model_id']} | {r['operation']} | {r['round']} | {r['layer']} | {label} | {value(estimate)} | {interval if interval is not None else 'NA'} | {r['independent_units']} | {r['inference_status']} | {value(r['p_value'])} | {value(r.get('holm_p_value'))} | {r.get('holm_family_size','—')}/{r.get('holm_estimable_family_size','—')} |\n"
        text+='\n同一行组的 p 是整个类别向量检验，不是逐标签检验。T3 verdict 为主检验；过程/票额为探索性，不能挑最显著标签替代主结局。\n'
    else:
        text+='本运行未配置推断；逐轮描述性 D 见 summary_effects。\n'
        text+='```json\n'+json.dumps(bundle['summary_effects'],ensure_ascii=False,indent=2)+'\n```\n'
    text+='\n## 初态分层\n\n| 操作 | T | S0模式 | 题数 | D |\n|---|---|---|---|---|\n'
    for r in bundle['stratified_effects']: text+=f"| {r['operation']} | {r['round']} | {r['initial_pattern']} | {r['items']} | {json.dumps(r['D'])} |\n"
    text+='\n分层在 S0 上预定，不能只保留出现大效应的子集或把分层变化直接称为机制。\n'
    text+='\n## 后果与准确率（辅助结局）\n\n| 操作 | 条件 | T | 完整/计划 | 准确率 |\n|---|---|---|---|---|\n'
    for r in bundle['condition_summaries']:
        text+=f"| {r['operation']} | {r['protocol']}-{r['variant']} | {r['round']} | {r['complete_items']}/{r['planned_items']} | {value(r['accuracy'])} |\n"
    text+='\nO→R 翻转、正确→错误和错误→正确（各率的分母见导出 *_items）：\n\n```json\n'+json.dumps(bundle['consequence_summaries'],ensure_ascii=False,indent=2)+'\n```\n'
    text+='\nS0→后续裁决变化（完整臂、条件可用分母，非主配对分母；每个率的分母分别保存）：\n\n```json\n'+json.dumps(bundle['initial_outcome_summaries'],ensure_ascii=False,indent=2)+'\n```\n'
    text+='\n## 人工验证与消息审计\n\n'
    for key,title in (('material_validation','原文—改写操纵核查'),('coding_agreement','后续回复内容编码'),('visibility_audit','冻结计划与实际消息路由')):
        text+='### '+title+'\n\n'
        text+=('```json\n'+json.dumps(bundle[key],ensure_ascii=False,indent=2)+'\n```\n\n' if key in bundle else '本运行未预定该项；不能写成已完成。\n\n')
    text+='缺少人工标注不是“通过”；自动审核不证明纯表达操纵。O/O 和中性改写是独立补充对照，不扣噪声地板。消息屏蔽估计路由干预后果，不自动识别中介路径；其余代理仍可能转述。\n'
    for key,title in (('noise_summaries','O/O 描述性波动'),('intervention_effects','定向消息干预（探索性）')):
        if bundle.get(key): text+='\n## '+title+'\n\n```json\n'+json.dumps(bundle[key],ensure_ascii=False,indent=2)+'\n```\n'
    return text
