"""在独立合成场景检查推断的零效应误报、区间覆盖和已知方向检出。

仅验证测量程序的模拟性质，不是正式实验结果，也不用于调参筛选数据。
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from controlled_mad.analysis import aggregate, four_cell_difference
from controlled_mad.inference import infer_effects


def simulate(seed, shift):
    rng = random.Random(seed)
    effects = []
    labels = ('A', 'B', 'C', 'D')
    for item in range(60):
        p = rng.uniform(.1, .6)
        repeats = []
        def draw(probability):
            def label(): return 'A' if rng.random() < probability else rng.choice(labels[1:])
            # 保留一次运行内三票可能高度相关的情况。
            votes = [label()] * 3 if rng.random() < .5 else [label() for _ in range(3)]
            return aggregate(votes, labels)
        for repeat in range(3):
            repeats.append(four_cell_difference(draw(min(.95, p + shift)), draw(p), draw(p), draw(p)))
        mean = {layer: {label: sum(d[layer][label] for d in repeats) / 3 for label in repeats[0][layer]}
                for layer in ('vote', 'verdict')}
        effects.append({'dataset_id': 'synthetic', 'model_id': 'sim', 'operation': 'sim',
                        'item_id': str(item), 'round': 3, 'D': mean})
    report = infer_effects(effects, {'resamples': 199, 'seed': 20261022, 'alpha': .05})
    return next(row for row in report if row['primary'])


def main():
    null = [simulate(10000 + i, 0) for i in range(100)]
    alternative = [simulate(20000 + i, .3) for i in range(50)]
    result = {
        'synthetic_only': True, 'items_per_simulation': 60, 'repeats': 3,
        'bootstrap_resamples': 199, 'null_simulations': 100, 'alternative_simulations': 50,
        'nominal_alpha': .05,
        'null_rejection_rate': sum(r['p_value'] < .05 for r in null) / len(null),
        'A_pointwise_null_interval_coverage': sum(r['confidence_intervals']['A'][0] <= 0 <= r['confidence_intervals']['A'][1] for r in null) / len(null),
        'alternative_rejection_rate': sum(r['p_value'] < .05 for r in alternative) / len(alternative),
        'note': 'Finite simulation precision; not universal calibration or a guarantee for real data. No TVD/noise-floor adjustment.'}
    path = ROOT / 'docs' / 'v10-statistical-validation.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    if not (result['null_rejection_rate'] <= .12 and result['A_pointwise_null_interval_coverage'] >= .85 and result['alternative_rejection_rate'] >= .7):
        raise SystemExit('Simulation diagnostics outside prespecified coarse acceptance bands')


if __name__ == '__main__': main()
