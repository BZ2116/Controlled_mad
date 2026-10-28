import json, math, re
from ipaddress import ip_address
from urllib.parse import urlsplit
from dataclasses import fields
from pathlib import Path
from .domain import AGENTS, OPERATIONS, ModelProfile, Intervention
from .identity import canonical_hash, safe_id

KEYS={'schema_version','dataset','profiles','debate_models','material_model','audit_model','material_audit','s0_batch','selection_seed','repeats','history_policy','message_order','operations','interventions','diagnostics','max_generation_attempts','max_attempts','budget','network_enabled','mock_scenario','prompts','experiment','subset'}
OPTIONAL_V10={'material_policy','material_gate','inference','intervention_subset','coding','complete_matrix','matrix_layout','matrix_protocol'}
KEYS |= OPTIONAL_V10

def validate_config(c):
    if not isinstance(c,dict): raise ValueError('配置必须是 JSON 对象')
    required=KEYS-{'mock_scenario','prompts','material_audit','audit_model','experiment','subset'}-OPTIONAL_V10
    if set(c)-KEYS or required-set(c): raise ValueError(f'配置字段不符，缺少 {sorted(required-set(c))}，未知 {sorted(set(c)-KEYS)}')
    if 'material_audit' in c and type(c['material_audit']) is not bool: raise ValueError('material_audit 必须为布尔值')
    if c.get('material_policy','legacy') not in ('legacy','strict_v10'): raise ValueError('未知材料资格策略')
    if 'material_gate' in c and type(c['material_gate']) is not bool: raise ValueError('material_gate 必须为布尔值')
    if c.get('material_policy')=='strict_v10' and not c.get('material_audit',True): raise ValueError('严格材料策略必须启用语义审核')
    inference=c.get('inference')
    if inference is not None:
        if (not isinstance(inference,dict) or set(inference)!={'resamples','seed','alpha'}
            or type(inference['resamples']) is not int or inference['resamples']<99
            or type(inference['seed']) is not int or type(inference['alpha']) not in (float,int)
            or not math.isfinite(inference['alpha']) or not 0<inference['alpha']<1):
            raise ValueError('推断配置须含 resamples>=99、整数 seed 和 0<alpha<1')
    for field in ('coding','intervention_subset'):
        if field in c: validate_subset(c[field])
    if 'subset' in c: validate_subset(c['subset'])
    if 'complete_matrix' in c and type(c['complete_matrix']) is not bool:
        raise ValueError('complete_matrix 必须为布尔值')
    if 'matrix_layout' in c and (not c.get('complete_matrix') or c['matrix_layout'] not in ('slot_first_v1','group_first_v2')):
        raise ValueError('matrix_layout 必须用于完整矩阵且显式选择已登记的布局版本')
    if 'matrix_protocol' in c and (not c.get('complete_matrix') or c['matrix_protocol'] not in ('single_pass_v1','equal_rounds_v10')):
        raise ValueError('matrix_protocol 必须用于完整矩阵并选择已登记的协议')
    if c.get('complete_matrix'):
        from .matrix import OPERATIONS as MATRIX_OPERATIONS
        if (c.get('material_policy')!='strict_v10' or not c.get('material_gate')
                or not c.get('material_audit',True) or c['repeats']!=1
                or set(c['operations'])!=set(MATRIX_OPERATIONS) or c['interventions']
                or c['diagnostics']['oo']['enabled'] or c.get('experiment','rq1_three_round')!='rq1_three_round'):
            raise ValueError('完整矩阵必须使用严格材料、全7操作、单次重复、三轮且无额外干预的协议')
    if c['schema_version']!=1 or c['history_policy']!='full': raise ValueError('只支持 schema 1 和 full 累计历史')
    mode=c.get('experiment','rq1_three_round')
    if mode not in ('rq1_three_round','rq2_five_round'): raise ValueError('未知实验模式')
    if mode=='rq2_five_round':
        subset=c.get('subset')
        if not isinstance(subset,dict) or set(subset) not in ({'size','seed'},{'item_ids'}): raise ValueError('五轮实验须配置 size/seed 或 item_ids 子集')
        if 'size' in subset and (type(subset['size']) is not int or subset['size']<1 or type(subset['seed']) is not int): raise ValueError('子集大小和种子无效')
        if 'item_ids' in subset and (not isinstance(subset['item_ids'],list) or not subset['item_ids'] or any(not isinstance(x,str) for x in subset['item_ids']) or len(set(subset['item_ids']))!=len(subset['item_ids'])): raise ValueError('题号清单无效')
        if c['interventions'] or c['diagnostics']['oo']['enabled']: raise ValueError('五轮补充实验只支持自然 MAD O/R')
        if c.get('material_audit',True) and c.get('material_policy')!='strict_v10': raise ValueError('旧五轮协议必须关闭修辞语义审核；审核须显式启用 strict_v10')
    elif 'subset' in c and c.get('material_policy')!='strict_v10': raise ValueError('旧三轮配置不支持 subset；新协议须显式 strict_v10')
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
            loopback=url.hostname=='localhost'
            if url.hostname and not loopback:
                try: loopback=ip_address(url.hostname).is_loopback
                except ValueError: pass
            if (url.scheme!='https' and not (url.scheme=='http' and loopback)) or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError('服务地址必须无凭据、无查询参数；外部地址使用 HTTPS，HTTP 仅限本机回环地址')
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
    expected={'S0','generate','annotate','audit','T1','T2','T3'} | ({'T4','T5'} if mode=='rq2_five_round' else set())
    if set(b['stage_calls'])!=expected or any(type(v) is not int or v<0 for v in b['stage_calls'].values()): raise ValueError('阶段预算必须为非负整数且匹配实验轮次')
    return c

def load_config(path):
    path=Path(path).resolve()
    c=validate_config(json.loads(path.read_text(encoding='utf-8-sig')))
    for entry,key in ((c['dataset'],'source'),(c,'mock_scenario')):
        if entry.get(key): entry[key]=str((path.parent/entry[key]).resolve())
    return c

def profile(c,name): return ModelProfile(profile_id=name,**c['profiles'][name])

def validate_subset(subset):
    if not isinstance(subset,dict) or set(subset) not in ({'size','seed'},{'item_ids'}): raise ValueError('子集须配置 size/seed 或 item_ids')
    if 'size' in subset and (type(subset['size']) is not int or subset['size']<1 or type(subset['seed']) is not int): raise ValueError('子集大小和种子无效')
    if 'item_ids' in subset and (not isinstance(subset['item_ids'],list) or not subset['item_ids'] or any(not isinstance(x,str) or not x for x in subset['item_ids']) or len(set(subset['item_ids']))!=len(subset['item_ids'])): raise ValueError('子集题号清单无效')
