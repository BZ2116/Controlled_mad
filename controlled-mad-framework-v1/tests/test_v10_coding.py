import copy
import unittest
from types import SimpleNamespace

from controlled_mad.coding import coding_packet, validate_codings, agreement_report


class Coding(unittest.TestCase):
    def packet(self):
        task = SimpleNamespace(task_id='task-1', stage='T1', messages=({'role': 'user', 'content': 'Peer says A: evidence'},),
                               context={'item_id': 'one', 'dataset_id': 'd', 'agent': 'agent_1', 'sender': 'agent_2',
                                        'round': 1, 'variant': 'ethos', 'protocol': 'shared', 'labels': ['A', 'B'], 'model_id': 'm'})
        graph = SimpleNamespace(tasks={'task-1': task}, selected={'task-1': 'parse-1'},
                                parsed=lambda tid: {'valid': True, 'value': {'label': 'A', 'comment': 'I assess the evidence.'}},
                                materials=[{'dataset_id': 'd', 'item_id': 'one', 'model_id': 'm',
                                            'original': 'Original evidence and qualifications.', 'label': 'A',
                                            'operation': 'ethos', 'rewritten': 'Displayed persuasive evidence.'}])
        return coding_packet(graph, ['one'])

    def test_packet_blinds_condition_and_never_fabricates_annotations(self):
        packet = self.packet()
        self.assertEqual(len(packet), 1)
        row = packet[0]
        self.assertNotIn('variant', row)
        self.assertNotIn('protocol', row)
        self.assertNotIn('gold', row)
        self.assertEqual(row['codes'], {})
        self.assertEqual(row['character_count'], 22)
        self.assertEqual(row['visible_messages'][0]['content'], 'Peer says A: evidence')

    def test_rewritten_history_always_has_original_reference_for_coders(self):
        row = self.packet()[0]
        self.assertEqual(row['frozen_original_reference']['comment'], 'Original evidence and qualifications.')
        self.assertEqual(row['frozen_original_reference']['label'], 'A')
        self.assertEqual(row['displayed_target_comment'], 'Displayed persuasive evidence.')

    def test_validate_unknown_ids_uncertainty_and_conflicting_duplicates(self):
        packet = self.packet()
        identifier = packet[0]['coding_id']
        codes = {k: 'uncertain' for k in ('stance_retained', 'evidence_reassessed', 'expression_reproduced', 'new_arguments', 'new_facts')}
        row = {'coding_id': identifier, 'coder_id': 'coder-a', 'codes': codes, 'rationale': 'Not enough evidence to decide.'}
        self.assertEqual(validate_codings([row], packet, [])[0], row)
        bad = copy.deepcopy(row); bad['coding_id'] = 'missing'
        with self.assertRaises(ValueError): validate_codings([bad], packet, [])
        bad = copy.deepcopy(row); bad['codes']['new_facts'] = 'maybe'
        with self.assertRaises(ValueError): validate_codings([bad], packet, [])
        with self.assertRaises(ValueError): validate_codings([row, row], packet, [])
        bad = copy.deepcopy(row); bad['codes']['new_facts'] = 'yes'
        with self.assertRaises(ValueError): validate_codings([bad], packet, [row])

    def test_agreement_missing_and_disagreement_not_auto_resolved(self):
        packet = self.packet()
        row = {'coding_id': packet[0]['coding_id'], 'coder_id': 'a',
               'codes': {k: 'yes' for k in ('stance_retained', 'evidence_reassessed', 'expression_reproduced', 'new_arguments', 'new_facts')},
               'rationale': 'quoted spans'}
        report = agreement_report(packet, [row], ('a', 'b'))
        self.assertEqual(report['double_coded'], 0)
        other = copy.deepcopy(row); other['coder_id'] = 'b'; other['codes']['new_facts'] = 'no'
        report = agreement_report(packet, [row, other], ('a', 'b'))
        self.assertEqual(report['double_coded'], 1)
        self.assertEqual(report['fields']['new_facts']['agreement'], 0)
        self.assertEqual(report['fields']['new_facts']['cohen_kappa'], 0)
        self.assertEqual(report['unresolved_disagreements'][0]['field'], 'new_facts')

