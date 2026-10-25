"""Source grounding and bounded format recovery; no live API calls."""

import copy
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from quiz_generation import generate_quiz, render_candidate
from test_functionality import bot, model_response


SOURCE = ("The Roman Republic traditionally began in 509 BCE. "
          "Two consuls held office for one year. "
          "The Senate influenced finance and foreign policy.")
CANDIDATE = {"questions": [
    {"question": "When did the Roman Republic traditionally begin?",
     "answer": "509 BCE.", "evidence": ["The Roman Republic traditionally began in 509 BCE."]},
    {"question": "How many consuls served together, and how long was their term?",
     "answer": "Two consuls, for one year.", "evidence": ["Two consuls held office for one year."]},
    {"question": "Which two areas did the Senate influence?",
     "answer": "Finance and foreign policy.", "evidence": ["The Senate influenced finance and foreign policy."]},
]}
APPROVED = {"supported": [True, True, True]}


class QuizGenerationTests(unittest.TestCase):
    def generate(self, responses):
        create = Mock(side_effect=responses)
        result = generate_quiz("mixed", [{"summary": "UNRELATED BIOLOGY"}],
                               [{"digest": SOURCE}], create=create, model="test-model", parse=lambda x: x)
        return result, create

    def test_numbering_is_rendered_by_code_and_evidence_stays_private(self):
        result, create = self.generate([CANDIDATE, APPROVED])
        self.assertTrue(bot.valid_quiz(result))
        self.assertNotIn('"evidence"', result["quiz"])
        self.assertNotIn("509 BCE", result["quiz"])
        for call in create.call_args_list:
            self.assertNotIn("UNRELATED BIOLOGY", call.kwargs["messages"][0]["content"])
        self.assertEqual(create.call_count, 2)

    def test_malformed_output_is_regenerated_once(self):
        result, create = self.generate([{"questions": "broken"}, CANDIDATE, APPROVED])
        self.assertTrue(bot.valid_quiz(result))
        self.assertEqual(create.call_count, 3)
        self.assertIn("Previous attempt was invalid", create.call_args_list[1].kwargs["messages"][0]["content"])

    def test_json_parse_error_is_retried(self):
        create = Mock(side_effect=["broken", CANDIDATE, APPROVED])

        def parse(value):
            if value == "broken":
                raise ValueError("bad JSON")
            return value

        result = generate_quiz("quiz", [{"summary": SOURCE}], [], create=create, model="test", parse=parse)
        self.assertTrue(bot.valid_quiz(result))

    def test_missing_key_and_wrong_question_count_never_publish(self):
        for value in ({"questions": []}, {"questions": CANDIDATE["questions"][:2]},
                      {"questions": [{"question": "Why?"}] * 3}):
            result, create = self.generate([value, value])
            self.assertEqual(result, {})
            self.assertEqual(create.call_count, 2)

    def test_invented_evidence_is_rejected_without_review(self):
        candidate = copy.deepcopy(CANDIDATE)
        candidate["questions"][0]["evidence"] = ["The Roman monarchy ended in 509 BCE."]
        result, create = self.generate([candidate, candidate])
        self.assertEqual(result, {})
        self.assertEqual(create.call_count, 2)

    def test_real_quote_does_not_bypass_semantic_review(self):
        unsupported = copy.deepcopy(CANDIDATE)
        unsupported["questions"][0]["question"] += " What event did it mark the end of?"
        unsupported["questions"][0]["answer"] += " The end of the monarchy."
        result, create = self.generate([unsupported, {"supported": [False, True, True]},
                                       CANDIDATE, APPROVED])
        self.assertTrue(bot.valid_quiz(result))
        self.assertNotIn("end of", result["quiz"])
        self.assertIn("failed source review", create.call_args_list[2].kwargs["messages"][0]["content"])

    def test_review_failure_is_closed_and_bounded(self):
        for verdict in ({"supported": [1, 1, 1]}, {"supported": [True]}, {},
                        {"supported": [False, True, True]}):
            result, create = self.generate([CANDIDATE, verdict, CANDIDATE, verdict])
            self.assertEqual(result, {})
            self.assertEqual(create.call_count, 4)

    def test_evidence_cannot_cross_separate_source_boundaries(self):
        candidate = copy.deepcopy(CANDIDATE)
        candidate["questions"][0]["evidence"] = ["one year. The Senate"]
        self.assertIsNone(render_candidate(candidate, ["Two consuls held office for one year.",
                                                      "The Senate influenced finance and foreign policy."]))

    def test_empty_source_never_calls_model(self):
        create = Mock()
        self.assertEqual(generate_quiz("quiz", [], [], create=create, model="test", parse=lambda x: x), {})
        create.assert_not_called()

    def test_actual_bot_helper_returns_compatible_quiz(self):
        with patch.object(bot.claude.messages, "create", side_effect=[
                model_response(CANDIDATE), model_response(APPROVED)]):
            self.assertTrue(bot.valid_quiz(bot.make_quiz("quiz", [], [{"digest": SOURCE}])))

    def test_text_upload_does_not_save_model_enrichment(self):
        response = NS(content=[NS(type="text", text="TOPIC: Roman Republic\nThe monarchy ended in 509 BCE.")])
        with patch.object(bot.claude.messages, "create", return_value=response):
            ingested = bot.ingest_attachment("history.txt", "text/plain", SOURCE.encode(), True)
        self.assertEqual(ingested["digest"], SOURCE)
        self.assertEqual(ingested["topic"], "Roman Republic")

    def test_image_extraction_explicitly_forbids_enrichment(self):
        response = NS(content=[NS(type="text", text="TOPIC: Notes\nVisible notes")])
        with patch.object(bot.claude.messages, "create", return_value=response) as create:
            bot.ingest_attachment("page.png", "image/png", b"fake-image", False)
        instruction = create.call_args.kwargs["messages"][0]["content"][1]["text"]
        self.assertIn("EXPLICITLY PRESENT", instruction)
        self.assertIn("instead of guessing", instruction)


if __name__ == "__main__":
    unittest.main()
