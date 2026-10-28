"""可审计的人工双人编码；未收到编码时保持缺失，不从关键词伪造判断。"""
from collections import Counter
from .identity import canonical_hash, safe_id

FIELDS = ('stance_retained', 'evidence_reassessed', 'expression_reproduced', 'new_arguments', 'new_facts')
VALUES = ('yes', 'no', 'uncertain')
CODEBOOK = {
    'version': 'v10-coding-1',
    'values': list(VALUES),
    'fields': {
        'stance_retained': '回复是否明确保留目标发送者的判断立场；仅标签相同不足以推断理由保留。',
        'evidence_reassessed': '是否引用或重新评估目标原评论中已有的证据，需指出文本依据。',
        'expression_reproduced': '是否复现目标评论的说服或排列特征；不得仅凭赞同判 yes。',
        'new_arguments': '回复是否提出目标原评论中未有的实质性推理前提或论据；不等于新事实。',
        'new_facts': '是否引入输入证据和目标原评论均未提供的可核事实或来源。',
    },
    'procedure': 'coder-a/coder-b 独立编码并引用文本；uncertain 是有效类别；分歧保留，待人工裁决，不自动多数表决。',
    'blindness': '移除表达版本、分支、金标和结果对比；可见原文可能使标注者推测表达，非完全盲法。',
    'unit': 'unique selected reply; shared T1 and shared O only coded once',
}


def coding_packet(graph, selected_item_ids):
    selected = set(selected_item_ids)
    rows = []
    references = {}
    versions = {}
    for material in graph.materials:
        key = tuple(material[k] for k in ('dataset_id', 'item_id', 'model_id'))
        reference = {'label': material['label'], 'comment': material['original']}
        if key in references and references[key] != reference: raise ValueError('同一冻结起点的原文引用不一致')
        references[key] = reference
        versions[(*key, material['operation'])] = material.get('rewritten')
    for task in graph.tasks.values():
        if task.stage not in ('T1', 'T2', 'T3', 'T4', 'T5') or task.context['item_id'] not in selected: continue
        parsed = graph.parsed(task.task_id)
        if not parsed or not parsed['valid']: continue
        ctx = task.context
        key = tuple(ctx[k] for k in ('dataset_id', 'item_id', 'model_id'))
        displayed = references[key]['comment'] if ctx['variant'] == 'O' else versions[(*key, ctx['variant'])]
        text = parsed['value']['comment']
        rows.append({'coding_id': canonical_hash([task.task_id, graph.selected[task.task_id]]),
                     **{k: ctx[k] for k in ('dataset_id', 'item_id', 'agent', 'round')},
                     'target_sender': ctx['sender'], 'reply': parsed['value'],
                     'frozen_original_reference': references[key], 'displayed_target_comment': displayed,
                     'visible_messages': list(task.messages), 'character_count': len(text),
                     'whitespace_word_count': len(text.split()), 'codes': {}, 'codebook_version': CODEBOOK['version']})
    return sorted(rows, key=lambda r: r['coding_id'])


def validate_codings(rows, packet, existing):
    identifiers = {row['coding_id'] for row in packet}
    prior = {(r['coding_id'], r['coder_id']): r for r in existing}
    seen = set()
    if not rows: raise ValueError('编码批次为空')
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'coding_id', 'coder_id', 'codes', 'rationale'}:
            raise ValueError('编码行必须含 coding_id/coder_id/codes/rationale')
        safe_id(row['coder_id'])
        if row['coding_id'] not in identifiers: raise ValueError('编码引用未知或已失效的回复')
        if not isinstance(row['codes'], dict) or set(row['codes']) != set(FIELDS) or any(v not in VALUES for v in row['codes'].values()):
            raise ValueError('五个编码字段必须各为 yes/no/uncertain')
        if not isinstance(row['rationale'], str) or not row['rationale'].strip(): raise ValueError('编码必须提供文本依据')
        key = (row['coding_id'], row['coder_id'])
        if key in seen or (key in prior and prior[key] != row): raise ValueError('重复或冲突的编码；原始记录不可覆盖')
        seen.add(key)
    return rows


def agreement_report(packet, codings, coders=('coder-a', 'coder-b')):
    if len(coders) != 2 or len(set(coders)) != 2: raise ValueError('需要两名不同标注者')
    identifiers = {row['coding_id'] for row in packet}
    indexed = {(r['coding_id'], r['coder_id']): r for r in codings if r['coding_id'] in identifiers}
    pairs = [(identifier, indexed[(identifier, coders[0])], indexed[(identifier, coders[1])])
             for identifier in sorted(identifiers) if all((identifier, coder) in indexed for coder in coders)]
    fields = {}; disagreements = []
    for field in FIELDS:
        counts_a = Counter(a['codes'][field] for _, a, b in pairs)
        counts_b = Counter(b['codes'][field] for _, a, b in pairs)
        n = len(pairs)
        observed = sum(a['codes'][field] == b['codes'][field] for _, a, b in pairs) / n if n else None
        expected = sum(counts_a[v] * counts_b[v] for v in VALUES) / n**2 if n else None
        kappa = (observed - expected) / (1 - expected) if n and expected < 1 else None
        fields[field] = {'pairs': n, 'agreement': observed, 'cohen_kappa': kappa,
                         'coder_a_distribution': dict(counts_a), 'coder_b_distribution': dict(counts_b)}
        for identifier, a, b in pairs:
            if a['codes'][field] != b['codes'][field]:
                disagreements.append({'coding_id': identifier, 'field': field,
                                      coders[0]: a['codes'][field], coders[1]: b['codes'][field]})
    return {'codebook_version': CODEBOOK['version'], 'packet_replies': len(packet), 'double_coded': len(pairs),
            'missing_by_coder': {coder: sum((identifier, coder) not in indexed for identifier in identifiers) for coder in coders},
            'fields': fields, 'unresolved_disagreements': disagreements}
