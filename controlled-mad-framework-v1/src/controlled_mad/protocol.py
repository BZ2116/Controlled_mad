"""版本化主实验协议：保留旧单轮I行为，显式启用v10等轮次I/M。"""
from collections import Counter
from pathlib import Path
import hashlib
from .identity import canonical_hash

AMENDMENT = 'single-pass-amendment'
LEGACY = 'legacy_three_round'
SINGLE_PASS = 'single_pass_v1'
EQUAL_ROUNDS = 'equal_rounds_v10'
STUDY_V2 = 'continued_peer_exchange_v2'


def load_amendment(store):
    if not (store.path / 'inputs' / f'{AMENDMENT}.json').exists(): return None
    record = store.get('inputs', AMENDMENT)
    if (record.get('mode') != SINGLE_PASS or record.get('config_hash') != canonical_hash(store.config)
            or record.get('repeats') != 1 or record.get('independent_rounds') != 1):
        raise ValueError('单次运行变更记录与冻结配置不一致')
    return record


def item_settings(config, amendment, dataset_id, item_id):
    if config.get('complete_matrix'):
        if config.get('matrix_protocol')==EQUAL_ROUNDS:
            return {'repeats': 1, 'independent_rounds': 3, 'design': EQUAL_ROUNDS}
        return {'repeats': 1, 'independent_rounds': 1, 'design': SINGLE_PASS}
    legacy = not amendment or [dataset_id, item_id] in amendment['completed_items']
    return {'repeats': config['repeats'] if legacy else 1,
            'independent_rounds': 3 if legacy else 1,
            'design': LEGACY if legacy else SINGLE_PASS}


def enable_single_pass(store):
    """仅在持运行锁时调用；追加协议变更，不修改 manifest 或既有实验对象。"""
    existing = load_amendment(store)
    if existing: return existing
    if (store.config.get('experiment') == 'rq2_five_round' or store.config['interventions']
            or store.config['diagnostics']['oo']['enabled']):
        raise ValueError('单次运行变更仅用于未启用补充干预和 O/O 诊断的三轮主实验')
    from .scheduler import Planner
    graph = Planner(store, persist=False).build()
    # Normal read-only graph recovery, not a raw/results/filesystem-wide audit.
    counts = Counter((m['dataset_id'], m['item_id']) for m in graph.materials)
    required = len(store.config['operations']) * len(store.config['debate_models'])
    completed = {key for key, count in counts.items() if count == required}
    completed.difference_update((m['dataset_id'], m['item_id']) for m in graph.materials
                                if m['status'] == 'pending')
    completed.difference_update((b['dataset_id'], b['item_id']) for b in graph.blocked)
    for tid, task in graph.tasks.items():
        parsed = graph.parsed(tid)
        if not parsed or not parsed['valid']:
            completed.discard((task.context['dataset_id'], task.context['item_id']))
    implementation = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                      for name in ('protocol.py', 'scheduler.py', 'engine.py', 'progress.py',
                                   'analysis.py', 'outcomes.py', 'inference.py', 'runner.py', 'cli.py')}
    record = {'mode': SINGLE_PASS, 'config_hash': canonical_hash(store.config),
              'repeats': 1, 'independent_rounds': 1, 'mad_rounds': 3,
              'completed_items': [list(key) for key in sorted(completed)],
              'event_boundary': len(store.all('events')), 'implementation': implementation,
              'partial_results': 'reuse repeat=0 T1 and M; archive other existing responses',
              'change': 'user_requested_one_repetition_and_one_round_independent_judgment'}
    store.put('inputs', AMENDMENT, record)
    store.event('protocol_amendment', mode=SINGLE_PASS, amendment_id=AMENDMENT,
                completed_items=len(completed), repeats=1, independent_rounds=1, mad_rounds=3)
    return record
