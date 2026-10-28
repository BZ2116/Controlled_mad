"""按题重采样完整四格效应向量；三票和同题重复不当作独立题。"""
import math
import random
from collections import defaultdict
from .identity import canonical_hash


def holm_adjust(values):
    if any(not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in values):
        raise ValueError('p 值必须为 0 到 1 的有限数')
    ordered = sorted(range(len(values)), key=values.__getitem__)
    adjusted = [0.0] * len(values)
    prior = 0.0
    for rank, index in enumerate(ordered):
        prior = max(prior, min(1.0, (len(values) - rank) * values[index]))
        adjusted[index] = prior
    return adjusted


def percentile(values, probability):
    values = sorted(values)
    position = (len(values) - 1) * probability
    lo = int(position)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def infer_effects(effects, settings):
    """中心化非参数 bootstrap 的整体零均值检验；区间为逐类别点态区间。

    重复先在题内平均，保留条件于冻结 S0/材料的估计目标。该检验是
    渐近 bootstrap 检验，不冒称表达处理的精确随机化检验。
    """
    grouped = defaultdict(list)
    for row in effects:
        if row['D'] is not None:
            key = (*tuple(row[k] for k in ('dataset_id', 'model_id', 'operation', 'round')),
                   row.get('design','legacy_three_round'))
            grouped[key].append(row)
    output = []
    for key, rows in sorted(grouped.items()):
        if len({r['item_id'] for r in rows}) != len(rows):
            raise ValueError('推断输入必须先在题内平均重复')
        seed = int(canonical_hash([settings['seed'], key[:4] if key[4]=='legacy_three_round' else key]), 16)
        # 两个层次共用相同的题目抽样；不拆开抽取一次运行中的三票。
        rng = random.Random(seed)
        samples = [[rng.randrange(len(rows)) for _ in rows] for _ in range(settings['resamples'])] if len(rows) >= 2 else []
        for layer in ('vote', 'verdict'):
            labels = tuple(rows[0]['D'][layer])
            values = [[r['D'][layer][label] for label in labels] for r in rows]
            n = len(values)
            mean = [sum(v[j] for v in values) / n for j in range(len(labels))]
            draws = [[sum(values[i][j] for i in indices) / n for j in range(len(labels))] for indices in samples]
            observed = sum(v * v for v in mean)
            null_statistics = [sum((draw[j] - mean[j]) ** 2 for j in range(len(labels))) for draw in draws]
            p = (1 + sum(v >= observed - 1e-14 for v in null_statistics)) / (len(draws) + 1) if draws else None
            intervals = {label: [percentile([d[j] for d in draws], settings['alpha'] / 2),
                                  percentile([d[j] for d in draws], 1 - settings['alpha'] / 2)]
                         for j, label in enumerate(labels)} if draws else None
            output.append(dict(zip(('dataset_id', 'model_id', 'operation', 'round', 'design'), key),
                               layer=layer, estimate=dict(zip(labels, mean)), confidence_intervals=intervals,
                               p_value=p, independent_units=n, interval_unit='item',
                               primary=key[3] == 3 and layer == 'verdict',
                               method='centered_item_bootstrap_vector_mean',
                               resamples=settings['resamples'], seed=settings['seed'], alpha=settings['alpha'],
                               interval_scope='pointwise; not simultaneous',
                               degenerate=all(v == values[0] for v in values),
                               inference_scope='conditional on frozen S0 and materials; iid items; exploratory process tests'))
    primary = [r for r in output if r['primary'] and r['p_value'] is not None]
    for row, adjusted in zip(primary, holm_adjust([r['p_value'] for r in primary])):
        row['holm_p_value'] = adjusted
        row['holm_family'] = 'all final-verdict dataset/model/operation tests in this run'
        row['holm_family_size'] = len(primary)
    return output
