"""完整四格配对分母上的分条件表现及 O→R 后果。"""
from collections import defaultdict
from .analysis import metrics, full_arm
from .protocol import LEGACY, SINGLE_PASS


def paired_outcomes(comparisons, gold):
    paired = []
    conditions = defaultdict(list)
    planned = defaultdict(set)
    for c in comparisons:
        ctx = c['context']
        base = {k: ctx[k] for k in ('dataset_id', 'item_id', 'model_id', 'operation', 'repeat')}
        base['design'] = ctx.get('design', LEGACY)
        complete = full_arm(c['O']) and full_arm(c['R'])
        actual = gold.get((ctx['dataset_id'], ctx['item_id']))
        for protocol in ('I', 'M'):
            for t in sorted(map(int,c['O'][protocol])):
                key = (ctx['dataset_id'], ctx['model_id'], ctx['operation'], protocol, t, base['design'])
                planned[key].add(ctx['item_id'])
                o = metrics(c['O'][protocol][str(t)], ctx['labels'])
                r = metrics(c['R'][protocol][str(t)], ctx['labels'])
                valid = complete and o['status'] == r['status'] == 'complete'
                oc = None if not valid or actual is None else o['verdict'] == actual
                rc = None if not valid or actual is None else r['verdict'] == actual
                paired.append({**base, 'comparison_id': c['comparison_id'], 'protocol': protocol, 'round': t,
                               'complete_case_pair': valid, 'gold': actual,
                               'O_verdict': o['verdict'], 'R_verdict': r['verdict'], 'O_correct': oc, 'R_correct': rc,
                               'or_flipped': o['verdict'] != r['verdict'] if valid else None,
                               'correct_to_wrong': oc and not rc if oc is not None else None,
                               'wrong_to_correct': not oc and rc if oc is not None else None})
                if valid:
                    for variant, m, correct in (('O', o, oc), ('R', r, rc)):
                        conditions[(*key, variant)].append({'item_id': ctx['item_id'], **m, 'correct': correct})
    summaries = []
    for key in sorted(planned):
        for variant in ('O', 'R'):
            rows = conditions[(*key, variant)]
            by_item = defaultdict(list)
            for row in rows:
                by_item[row['item_id']].append(row)
            item_votes = []
            item_verdicts = []
            item_accuracy = []
            for repeats in by_item.values():
                for field, destination in (('vote', item_votes), ('verdict_vector', item_verdicts)):
                    destination.append({k: sum(r[field][k] for r in repeats) / len(repeats) for k in repeats[0][field]})
                known = [r['correct'] for r in repeats if r['correct'] is not None]
                if known: item_accuracy.append(sum(known) / len(known))
            average = lambda xs: {k: sum(x[k] for x in xs) / len(xs) for k in xs[0]} if xs else None
            summaries.append(dict(zip(('dataset_id', 'model_id', 'operation', 'protocol', 'round', 'design'), key),
                                  is_final=(key[4]==(1 if key[3]=='I' and key[5]==SINGLE_PASS else 3)),
                                  variant=variant, planned_items=len(planned[key]), complete_items=len(by_item),
                                  complete_repeats=len(rows), coverage=len(by_item) / len(planned[key]),
                                  gold_items=len(item_accuracy), accuracy=sum(item_accuracy) / len(item_accuracy) if item_accuracy else None,
                                  vote_distribution=average(item_votes), verdict_distribution=average(item_verdicts),
                                  denominator_scope='complete pairs within design; repeats averaged within item; single_pass I ends at T1',
                                  no_majority_accuracy_policy='incorrect when gold is available'))
    consequence_groups = defaultdict(lambda: defaultdict(list))
    for row in paired:
        key = tuple(row[k] for k in ('dataset_id', 'model_id', 'operation', 'protocol', 'round', 'design'))
        if row['complete_case_pair']: consequence_groups[key][row['item_id']].append(row)
    consequences = []
    for key, items in sorted(consequence_groups.items()):
        summary = dict(zip(('dataset_id', 'model_id', 'operation', 'protocol', 'round', 'design'), key))
        for field in ('or_flipped', 'correct_to_wrong', 'wrong_to_correct'):
            means = []
            for repeats in items.values():
                values = [r[field] for r in repeats if r[field] is not None]
                if values: means.append(sum(values) / len(values))
            summary[field + '_rate'] = sum(means) / len(means) if means else None
            summary[field + '_items'] = len(means)
        consequences.append(summary)
    return {'paired_outcomes': paired, 'condition_summaries': summaries, 'consequence_summaries': consequences}
