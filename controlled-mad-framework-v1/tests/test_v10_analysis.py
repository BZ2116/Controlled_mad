import unittest

from controlled_mad.analysis import aggregate
from controlled_mad.inference import holm_adjust, infer_effects
from controlled_mad.outcomes import paired_outcomes


def snap(votes):
    return {'replies': [{'agent': f'agent_{i+1}', 'value': {'label': label}} for i, label in enumerate(votes)]}


def comparison(item, original, rewritten, repeat=0):
    return {'comparison_id': f'{item}-{repeat}',
            'context': {'dataset_id': 'd', 'item_id': item, 'model_id': 'm', 'operation': 'ethos',
                        'repeat': repeat, 'labels': ['A', 'B', 'C'], 'initial_labels': ['A', 'A', 'B']},
            'O': {p: {str(t): snap(original) for t in range(1, 4)} for p in ('I', 'M')},
            'R': {p: {str(t): snap(rewritten) for t in range(1, 4)} for p in ('I', 'M')}}


class V10Analysis(unittest.TestCase):
    def test_flip_distinguishes_original_to_rewrite_from_initial_change(self):
        result = paired_outcomes([comparison('one', ['B']*3, ['A']*3)], {('d', 'one'): 'A'})
        row = next(r for r in result['paired_outcomes'] if r['round'] == 3 and r['protocol'] == 'M')
        self.assertEqual(row['O_verdict'], 'B')
        self.assertEqual(row['R_verdict'], 'A')
        self.assertTrue(row['or_flipped'])
        self.assertTrue(row['wrong_to_correct'])
        self.assertFalse(row['O_correct'])
        self.assertTrue(row['R_correct'])

    def test_missing_excluded_and_repeats_do_not_weight_items_twice(self):
        comps = [comparison('one', ['A']*3, ['A']*3, repeat=i) for i in range(3)]
        comps += [comparison('two', ['B']*3, ['A', 'B', 'C']), comparison('missing', ['A']*3, ['A']*3)]
        comps[-1]['R']['M']['3'] = None
        result = paired_outcomes(comps, {('d', k): 'A' for k in ('one', 'two', 'missing')})
        row = next(r for r in result['condition_summaries'] if r['round'] == 3 and r['protocol'] == 'M' and r['variant'] == 'O')
        self.assertEqual(row['planned_items'], 3)
        self.assertEqual(row['complete_items'], 2)
        self.assertEqual(row['complete_repeats'], 4)
        self.assertAlmostEqual(row['accuracy'], .5)
        row = next(r for r in result['condition_summaries'] if r['round'] == 3 and r['protocol'] == 'M' and r['variant'] == 'R')
        self.assertAlmostEqual(row['verdict_distribution']['no_majority'], .5)
        self.assertAlmostEqual(row['accuracy'], .5)

    def test_holm_monotone_and_validated(self):
        self.assertEqual(holm_adjust([.01, .04, .03]), [.03, .06, .06])
        with self.assertRaises(ValueError): holm_adjust([float('nan')])

    def test_inference_null_and_known_effect_preserve_label_vectors(self):
        def rows(value):
            return [{'dataset_id': 'd', 'model_id': 'm', 'operation': 'ethos', 'item_id': str(i), 'round': 3,
                     'D': {'vote': {'A': value, 'B': -value},
                           'verdict': {'A': value, 'B': -value, 'no_majority': 0.0}}} for i in range(20)]
        settings = {'resamples': 199, 'seed': 42, 'alpha': .05}
        null = infer_effects(rows(0), settings)
        effect = infer_effects(rows(1), settings)
        self.assertTrue(all(r['p_value'] == 1 for r in null))
        primary = next(r for r in effect if r['layer'] == 'verdict')
        self.assertLess(primary['holm_p_value'], .05)
        self.assertEqual(primary['confidence_intervals']['A'], [1.0, 1.0])
        self.assertEqual(primary['independent_units'], 20)
        self.assertEqual(primary['interval_unit'], 'item')
        self.assertEqual(infer_effects(rows(1)[:1], settings)[0]['p_value'], None)

