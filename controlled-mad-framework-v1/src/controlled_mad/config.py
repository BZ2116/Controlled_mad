import json, math, re
from urllib.parse import urlsplit
from dataclasses import fields
from pathlib import Path
from .domain import AGENTS, OPERATIONS, ModelProfile, Intervention
from .identity import canonical_hash, safe_id

KEYS={'schema_version','dataset','profiles','debate_models','material_model','audit_model','material_audit','s0_batch','selection_seed','repeats','history_policy','message_order','operations','interventions','diagnostics','max_generation_attempts','max_attempts','budget','network_enabled','mock_scenario','prompts'}

def validate_config(c):
    if not isinstance(c,dict): raise ValueError('配置必须是 JSON 对象')
    required=KEYS-{'mock_scenario','prompts','material_audit','audit_model'}
    if set(c)-KEYS or required-set(c): raise ValueError(f'配置字段不符，缺少 {sorted(required-set(c))}，未知 {sorted(set(c)-KEYS)}')
    if 'material_audit' in c and type(c['material_audit']) is not bool: raise ValueError('material_audit 必须为布尔值')
    if c['schema_version']!=1 or c['history_policy']!='full': raise ValueError('只支持 schema 1 和 full 累计历史')
    if sorted(c['message_order'])!=list(AGENTS): raise ValueError('消息顺序必须恰含三个 agent')
    for k in ('repeats','max_generation_attempts','max_attempts'):
        if type(c[k]) is not int or c[k]<1: raise ValueError(f'{k} 必须为正整数')
    if type(c['selection_seed']) is not int or type(c['network_enabled']) is not bool: raise ValueError('种子或网络开关类型无效')
    if not c['operations'] or len(set(c['operations']))!=len(c['operations']) or set(c['operations'])-set(OPERATIONS): raise ValueError('表达操作无效')
    if set(c['dataset'])!={'adapter','source'} or c['dataset']['adapter'] not in ('jsonl','averitec','mmlu_pro','bbq'): raise ValueError('数据适配配置无效')
    profiles=c['profiles']
    if not isinstance(profiles,dict) or not profiles: raise ValueError('缺少模型配置')
    for name,p in profiles.items():
        safe_id(name)
        if set(p)-{f.name for f in fields(ModelProfile)} or 'profile_id' in p: raise ValueError('模型配置含未知或保留字段')
        obj=ModelProfile(profile_id=name,**p)
        if obj.provider not in ('mock','openai','anthropic'): raise ValueError('未知模型后端')
        if obj.reasoning_effort is not None and (obj.provider!='openai' or obj.reasoning_effort not in ('low','medium','high','max')): raise ValueError('reasoning_effort 仅支持 OpenAI-compatible 的 low/medium/high/max')
        if obj.max_tokens_field not in ('max_tokens','max_completion_tokens') or (obj.provider=='anthropic' and obj.max_tokens_field!='max_tokens'): raise ValueError('模型输出上限字段不受支持')
        if not isinstance(obj.model,str) or not obj.model.strip() or type(obj.max_tokens) is not int or type(obj.context_limit) is not int or obj.max_tokens<1 or obj.context_limit<obj.max_tokens: raise ValueError('模型参数无效')
        if not isinstance(obj.timeout,(int,float)) or not math.isfinite(obj.timeout) or obj.timeout<=0: raise ValueError('超时必须为有限正数')
        if obj.temperature is not None and (not isinstance(obj.temperature,(int,float)) or not math.isfinite(obj.temperature) or not 0<=obj.temperature<=2): raise ValueError('temperature 必须为 0 到 2 的有限数或 null')
        if type(obj.supports_seed) is not bool or (obj.seed is not None and type(obj.seed) is not int): raise ValueError('seed 能力声明或种子类型无效')
        if obj.seed is not None and not obj.supports_seed: raise ValueError('服务未声明支持 seed，不可静默省略')
        if obj.provider!='mock':
            url=urlsplit(obj.base_url)
            if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment: raise ValueError('服务地址必须是无凭据、无查询参数的 HTTPS 基础地址')
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',obj.credential_env): raise ValueError('必须提供凭据环境变量名，不能填写实际凭据')
    if not c['debate_models'] or len(set(c['debate_models']))!=len(c['debate_models']): raise ValueError('被评模型列表无效')
    used=[*c['debate_models'],c['material_model']]
    if c.get('material_audit',True):
        if 'audit_model' not in c: raise ValueError('启用材料审核时必须配置 audit_model')
        used.append(c['audit_model'])
    elif 'audit_model' in c and c['audit_model'] not in profiles: raise ValueError('引用了未定义的审核模型')
    if any(x not in profiles for x in used): raise ValueError('引用了未定义模型')
    safe_id(c['s0_batch'])
    rules=[Intervention(**r) for r in c['interventions']]
    if len({r.rule_id for r in rules})!=len(rules): raise ValueError('干预 ID 重复')
    for r in rules: safe_id(r.rule_id)
    if set(c['diagnostics'])!={'oo'} or set(c['diagnostics']['oo'])!={'enabled','repeats'}: raise ValueError('诊断配置无效')
    oo=c['diagnostics']['oo']
    if type(oo['enabled']) is not bool or type(oo['repeats']) is not int or oo['repeats']<1: raise ValueError('诊断重复数无效')
    b=c['budget']
    if set(b)!={'max_calls','max_tokens','stage_calls'} or any(type(b[k]) is not int or b[k]<1 for k in ('max_calls','max_tokens')): raise ValueError('预算必须有正整数调用和 token 上限')
    if set(b['stage_calls'])!={'S0','generate','annotate','audit','T1','T2','T3'} or any(type(v) is not int or v<0 for v in b['stage_calls'].values()): raise ValueError('阶段预算必须为非负整数')
    return c

def load_config(path):
    path=Path(path).resolve()
    c=validate_config(json.loads(path.read_text()))
    for entry,key in ((c['dataset'],'source'),(c,'mock_scenario')):
        if entry.get(key): entry[key]=str((path.parent/entry[key]).resolve())
    return c

def profile(c,name): return ModelProfile(profile_id=name,**c['profiles'][name])
