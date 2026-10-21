import json
import tempfile
import unittest
from pathlib import Path

from controlled_mad.datasets.averitec import convert as convert_averitec
from controlled_mad.datasets.bbq import BBQAdapter, convert as convert_bbq
from controlled_mad.datasets.mmlu_pro import MMLUProAdapter, convert as convert_mmlu_pro
from controlled_mad.api import validate
from controlled_mad.identity import plain


class DatasetAdapters(unittest.TestCase):
    def test_mmlu_pro_keeps_question_choices_and_strata_without_gold_leak(self):
        bundle = convert_mmlu_pro([{
            "question": "Which option is correct?",
            "options": [f"choice {letter}" for letter in "ABCDEFGHIJ"],
            "answer": "C",
            "category": "physics",
            "cot_content": "SECRET rationale",
        }])

        item = bundle.items[0]
        self.assertEqual(item.labels, tuple("ABCDEFGHIJ"))
        self.assertIn("Which option is correct?", item.task_input)
        self.assertIn("C. choice C", item.task_input)
        self.assertEqual(dict(item.metadata)["category"], "physics")
        self.assertEqual(bundle.gold[0].label, "C")
        self.assertNotIn("SECRET", json.dumps(plain(item)))

    def test_mmlu_pro_maps_answer_index_and_rejects_bad_choice_count(self):
        bundle = convert_mmlu_pro([{
            "question": "Pick one",
            "options": ["first", "second", "third"],
            "answer_index": 1,
            "category": "math",
        }, {
            "question": "Broken",
            "options": ["only one"],
            "answer": "A",
        }])

        self.assertEqual(len(bundle.items), 1)
        self.assertEqual(bundle.gold[0].label, "B")
        self.assertEqual(len(bundle.rejected), 1)

    def test_bbq_uses_context_question_and_native_answer_index(self):
        bundle = convert_bbq([{
            "example_id": "bbq-001",
            "context": "A short scenario.",
            "question": "Who did the action?",
            "ans0": "Person A",
            "ans1": "Person B",
            "ans2": "Unknown",
            "label": 2,
            "category": "gender",
            "context_condition": "ambig",
            "question_polarity": "neg",
        }])

        item = bundle.items[0]
        self.assertIn("A short scenario.", item.task_input)
        self.assertIn("Who did the action?", item.task_input)
        self.assertIn("C. Unknown", item.task_input)
        self.assertEqual(bundle.gold[0].label, "C")
        self.assertEqual(dict(item.metadata)["context_condition"], "ambig")

    def test_bbq_accepts_answer_list_and_writes_rejections(self):
        bundle = convert_bbq([{
            "example_id": "bbq-002",
            "context": "Context",
            "question": "Question?",
            "answers": ["one", "two", "three"],
            "answer_label": "B",
            "category": "age",
            "context_condition": "disambig",
        }, {
            "context": "Context",
            "question": "Missing options?",
            "answers": ["only one"],
            "label": 0,
        }])

        self.assertEqual(bundle.gold[0].label, "B")
        self.assertEqual(len(bundle.rejected), 1)

    def test_dataset_adapters_load_json_and_jsonl(self):
        mmlu_row = {"question": "Q", "options": ["a", "b"], "answer": "A"}
        bbq_row = {"example_id": "b1", "context": "C", "question": "Q", "answers": ["a", "b", "c"], "label": 0}
        with tempfile.TemporaryDirectory() as temp_dir:
            mmlu_path = Path(temp_dir) / "mmlu.jsonl"
            mmlu_path.write_text(json.dumps(mmlu_row) + "\n", encoding="utf-8")
            bbq_path = Path(temp_dir) / "bbq.json"
            bbq_path.write_text(json.dumps([bbq_row]), encoding="utf-8")

            mmlu_bundle = MMLUProAdapter().load(mmlu_path)
            bbq_bundle = BBQAdapter().load(bbq_path)

        self.assertEqual(len(mmlu_bundle.items), 1)
        self.assertEqual(len(bbq_bundle.items), 1)
        self.assertTrue(mmlu_bundle.source_hash)
        self.assertTrue(bbq_bundle.source_hash)

    def test_validate_dispatches_to_new_adapters_and_reports_strata(self):
        mmlu_row = {"question": "Q", "options": ["a", "b"], "answer": "A", "category": "physics"}
        bbq_row = {"example_id": "b1", "context": "C", "question": "Q", "answers": ["a", "b", "c"], "label": 0, "context_condition": "ambig"}
        template = json.loads((Path(__file__).parents[1] / "configs" / "demo.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for dataset, adapter, row in (("mmlu", "mmlu_pro", mmlu_row), ("bbq", "bbq", bbq_row)):
                data_path = root / f"{dataset}.json"
                data_path.write_text(json.dumps([row]), encoding="utf-8")
                config = dict(template)
                config["dataset"] = {"adapter": adapter, "source": data_path.name}
                config_path = root / f"{dataset}-config.json"
                config_path.write_text(json.dumps(config), encoding="utf-8")

                result = validate(config_path)

                self.assertEqual(result["items"], 1)
                self.assertEqual(result["dataset_id"], adapter)
                expected_stratum = "category" if adapter == "mmlu_pro" else "context_condition"
                self.assertEqual(sum(result["strata"][expected_stratum].values()), 1)

    def test_averitec_task_input_is_dataset_neutral_and_gold_stays_separate(self):
        bundle = convert_averitec([{
            "claim": "Claim",
            "label": "Supported",
            "justification": "SECRET",
            "questions": [{"question": "Question", "answers": [{"answer": "Evidence"}]}],
        }])

        self.assertIn("Claim", bundle.items[0].task_input)
        self.assertIn("Evidence", bundle.items[0].task_input)
        self.assertTrue(bundle.items[0].task_input.startswith("Claim:"))
        self.assertIn("\nEvidence:\n", bundle.items[0].task_input)
        self.assertNotIn("SECRET", json.dumps(plain(bundle.items)))


if __name__ == "__main__":
    unittest.main()
