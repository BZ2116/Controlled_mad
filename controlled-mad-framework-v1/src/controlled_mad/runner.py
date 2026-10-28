"""显式加载凭据、可续跑的先材料后讨论入口；从不打印或持久化密钥。"""
import os
import re
from pathlib import Path
from . import api
from .config import load_config
from .identity import canonical_hash
from .pipeline import item_pipeline_enabled


def load_credentials(path, profile):
    values = {}
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        # 用户明确指定的示例文件也可能用 # 注释保存赋值；不自动发现或加载文件。
        match = re.fullmatch(r'\s*#?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*', line)
        if not match: continue
        name, value = match.groups()
        value = value.strip().strip('\"\'')
        if name in values: raise ValueError('凭据文件存在重复变量，拒绝猜测')
        values[name] = value
    if values.get('OPENAI_BASE_URL', profile['base_url']).rstrip('/') != profile['base_url'].rstrip('/'):
        raise ValueError('凭据文件服务地址与冻结配置不符，未发送请求')
    if values.get('MODEL_NAME', profile['model']) != profile['model']:
        raise ValueError('凭据文件模型与冻结配置不符，未发送请求')
    keys = {values[name] for name in (profile['credential_env'], 'MODEL_API_KEY', 'OPENAI_API_KEY') if values.get(name)}
    if len(keys) != 1: raise ValueError('指定文件没有唯一可用凭据')
    key = keys.pop()
    if re.search(r'your[-_ ]|replace|example|xxx|\.\.\.|<|>', key, re.I):
        raise ValueError('指定文件只含占位凭据')
    os.environ[profile['credential_env']] = key


def run_main(config_path, run_id, runs_root=None, credentials_file=None, prepare_only=False, concurrency=3, progress=None,item_pipeline=False,single_pass=False,reuse_matrix_source=None,question_batch_size=None,continue_on_error=False):
    config = load_config(config_path)
    if not config.get('material_gate') or config.get('material_policy') != 'strict_v10':
        raise ValueError('main-run 只接受显式严格材料资格及全局冻结配置')
    root = Path(runs_root) if runs_root else Path.cwd() / 'runs'
    if reuse_matrix_source:
        if progress: progress({'event':'prepare','stage':'只读来源运行并继承兼容回复','completed':0})
        from .matrix_revision import prepare_revision
        prepare_revision(config_path,reuse_matrix_source,run_id,root)
    if not prepare_only:
        if credentials_file:
            profiles = [config['profiles'][name] for name in {*config['debate_models'], config['material_model'], config['audit_model']}]
            if len({(p.get('base_url'), p['model'], p.get('credential_env')) for p in profiles}) != 1:
                raise ValueError('单凭据文件启动仅用于同一提供方/模型；多个模型请显式设置各环境变量')
            load_credentials(credentials_file, profiles[0])
        for name in {*config['debate_models'], config['material_model'], config['audit_model']}:
            p = config['profiles'][name]
            if p['provider'] != 'mock' and not os.environ.get(p['credential_env']):
                raise ValueError(f"凭据环境变量未设置：{p['credential_env']}")
    if (root / run_id).exists():
        store = api.open_run(run_id, root)
        if canonical_hash(store.config) != canonical_hash(config): raise ValueError('已有运行与本次配置不符，不能覆盖冻结协议')
    else:
        store = api.create_run(config_path, run_id, root)
    if prepare_only:
        return {'phase': 'prepared', 'run_id': run_id, 'path': str(store.path), 'plan': api.plan(config_path)}
    if (store.path / 'inputs' / 'material-freeze.json').exists():
        frozen=store.get('inputs','material-freeze')
        if not frozen['counts'].get('eligible',0):
            return {'phase':'no_eligible_materials','run_id':run_id,'material_freeze':frozen,'status':api.status(store)}
    executions = []
    pipeline=item_pipeline or config.get('complete_matrix',False) or item_pipeline_enabled(store)
    for phase in (('item_pipeline',) if pipeline else ('materials', 'debate')):
        if phase == 'materials' and (store.path / 'inputs' / 'material-freeze.json').exists(): continue
        result = api.execute_ready(store, allow_network=True, progress=progress,
                                   auto_retry_unknown=True, concurrency=concurrency, retry_http=True,
                                   item_pipeline=item_pipeline,single_pass=single_pass,question_batch_size=question_batch_size,continue_on_error=continue_on_error)
        executions.append({'phase': phase, **result})
        if progress: progress({'event':'prepare','stage':'汇总运行状态','completed':result['executed']})
        state = api.status(store)
        from .failures import enabled, export_failures
        if enabled(store):
            failures=export_failures(store)
            if result['stop'] in ('transport_unknown','backend_error'):
                return {'phase':'paused_service','run_id':run_id,'executions':executions,'status':state,'failures':failures}
            items=state['progress']['items']
            if items.get('processed',items['complete'])==items['total'] and items.get('skipped',0):
                if progress: progress({'event':'prepare','stage':'导出已完成结果、缺格及跳过记录','completed':result['executed']})
                analysis=api.analyze_and_export(store)
                return {'phase':'completed_with_failures','run_id':run_id,'executions':executions,'status':state,
                        'failures':failures,'analysis':analysis,
                        'verification':{'mode':'checkpoint_and_on_read','full_scan':False}}
        if result['stop'] in ('transport_unknown', 'backend_error') or state['invalid'] or state['transport_unknown']:
            return {'phase': 'needs_repair', 'run_id': run_id, 'executions': executions, 'status': state}
        if config.get('complete_matrix') and state['progress']['items']['complete']!=state['progress']['items']['total']:
            return {'phase':'incomplete_matrix','run_id':run_id,'executions':executions,
                    'status':state,'matrix':api.matrix_status(store)}
        if phase in ('materials','item_pipeline'):
            if progress: progress({'event':'prepare','stage':'归档材料资格清单','completed':result['executed']})
            frozen = api.freeze_materials(store)
            if not frozen['counts'].get('eligible', 0):
                return {'phase': 'no_eligible_materials', 'run_id': run_id, 'material_freeze': frozen, 'status': state}
    state = api.status(store)
    if state['blocked_groups'] or state['ready']:
        return {'phase': 'incomplete', 'run_id': run_id, 'executions': executions, 'status': state}
    if progress: progress({'event':'prepare','stage':'生成离线报告','completed':sum(r['executed'] for r in executions)})
    verified = {'mode':'checkpoint_and_on_read','full_scan':False}
    analysis = api.analyze_and_export(store)
    coding = api.export_coding(store) if config.get('coding') else None
    return {'phase': 'completed', 'run_id': run_id, 'executions': executions, 'status': state,
            'verification': verified, 'analysis': analysis, 'coding': coding}
