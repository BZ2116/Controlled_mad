"""恢复入口与跨文件完整性核验。修复动作仍由统一 engine 执行。"""
from .engine import recover, reparse, new_attempt, fork_revision, status
from .identity import canonical_hash, plain
from .config import profile
from .store import IntegrityError


def verify_run(store):
    report=store.verify()
    tasks={t['task_id']:t for t in store.all('tasks')}
    parses={key:store.get('parses',key) for key in store.ids('parses')}
    for key,task in tasks.items():
        if task['prompt_hash']!=canonical_hash(task['messages']): raise IntegrityError('任务提示哈希不符')
        identity={'stage':task['stage'],'profile':plain(profile(store.config,task['profile_id'])),'messages':task['messages'],'dependencies':task['dependencies'],'context':task['context']}
        if canonical_hash(identity)!=key: raise IntegrityError('任务身份哈希不符')
        if any(dep not in tasks for dep in task['dependencies']): raise IntegrityError('任务引用不存在的前置槽位')
        if set(task['context'].get('delivered',[]))&set(task['context'].get('blocked',[])): raise IntegrityError('同一条消息同时被投递和屏蔽')
    for key,p in parses.items():
        if key!=canonical_hash(p) or p['task_id'] not in tasks: raise IntegrityError('解析身份或任务引用不符')
        raw=store.read_blob('raw',p['attempt_id'],'.bin')
        if p['raw_hash']!=canonical_hash(raw.hex()): raise IntegrityError('解析未指向原始响应')
        if p['valid'] and tasks[p['task_id']]['stage'] in ('S0','T1','T2','T3','T4','T5','generate') and p['value']['label'] not in tasks[p['task_id']]['context']['labels']:
            raise IntegrityError('解析中的投票标签越界')
    for tid,pid in store.selection().items():
        if pid not in parses or parses[pid]['task_id']!=tid: raise IntegrityError('选定解析引用不一致')
    return {**report,'verified_tasks':len(tasks),'verified_parses':len(parses)}

def rebuild_state(store):
    with store.lock():
        verify_run(store)
        recovered=recover(store)
        return {'recovered':recovered,'status':status(store)}
