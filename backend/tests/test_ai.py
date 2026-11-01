"""Algorithm tests use deterministic provider responses, not fabricated AI results."""
import json
import unittest
from unittest.mock import patch

from app.ai import AIError, _chunks, _clean_words, _json_payload, _score, analyze


def words_for(duration=240):
    return [{"start": float(i), "end": i + 0.7,
             "text": "Победа!" if i % 10 == 9 else f"слово{i}"}
            for i in range(duration)]


def candidate(start, end, value=8):
    return {"start": start, "end": end, "title": "Неожиданный исход",
            "reason": "Полная история с развязкой",
            "rubric": dict.fromkeys(["hook", "context", "emotion", "novelty", "payoff"], value)}


class AnalysisTests(unittest.TestCase):
    def test_hierarchical_ranking_and_dedup(self):
        source = words_for()
        first, second = candidate(10, 65), candidate(120, 175, 6)
        duplicate = candidate(15, 68, 7)
        reranked = [{"id": 0, "rubric": first["rubric"], "reason": "Первое место"},
                    {"id": 1, "rubric": second["rubric"], "reason": "Второе место"}]
        with patch("app.ai._llm", side_effect=[{"candidates": [first, duplicate, second]}, {"ranked": reranked}]) as provider:
            result = analyze(source, 240, {"analysis_provider": "ollama", "max_clips": 2})
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(len(result), 2)
        self.assertGreater(result[0]["score"], result[1]["score"])
        self.assertEqual(result[0]["reason"], "Первое место")
        self.assertTrue(all(30 <= clip["end"] - clip["start"] <= 90 for clip in result))
        self.assertTrue(all(clip["start"] <= 240 and clip["end"] <= 240 for clip in result))

    def test_invalid_model_times_do_not_create_exports(self):
        invalid = candidate(float("nan"), 80)
        outside = candidate(1000, 1050)
        with patch("app.ai._llm", return_value={"candidates": [invalid, outside]}):
            with self.assertRaises(AIError):
                analyze(words_for(), 240, {"analysis_provider": "ollama"})

    def test_long_stream_reranking_has_bounded_batches(self):
        batches = []
        def respond(system, prompt, config):
            if 'Propose up to 3' in prompt:
                import re
                low = float(re.search(r'Window: ([0-9.]+)', prompt)[1])
                return {'candidates': [candidate(low + n * 100, low + n * 100 + 50) for n in range(3)]}
            batch = json.loads(prompt.split('\n', 1)[1])
            batches.append(batch)
            return {'ranked': [{'id': item['id'], 'rubric': candidate(0, 50)['rubric'], 'reason': 'Проверено'} for item in batch]}
        with patch('app.ai._llm', side_effect=respond):
            result = analyze(words_for(2400), 2400, {'analysis_provider':'ollama','max_clips':5})
        self.assertEqual(len(result), 5)
        self.assertGreater(len(batches), 1)
        self.assertTrue(all(1 <= len(batch) <= 8 for batch in batches))

    def test_provider_failure_does_not_fallback_to_heuristic(self):
        with patch("app.ai._llm", side_effect=AIError("Ollama недоступна")):
            with self.assertRaisesRegex(AIError, "недоступна"):
                analyze(words_for(), 240, {"analysis_provider": "ollama"})

    def test_explicit_heuristic_short_source(self):
        with patch("app.ai._llm") as provider:
            result = analyze(words_for(18), 18, {"analysis_provider": "heuristic"})
        provider.assert_not_called()
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]["start"], result[0]["end"]), (0, 18))
        self.assertLessEqual(result[0]["score"], 64)
        self.assertIn("Контекст моделью не оценивался", result[0]["reason"])

    def test_chunking_covers_all_words_and_bounds_context(self):
        source = words_for(8500)
        chunks = list(_chunks(source))
        covered = set()
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1800)
            self.assertLessEqual(chunk[-1]["end"] - chunk[0]["start"], 480)
            covered.update(word["start"] for word in chunk)
        self.assertEqual(covered, {word["start"] for word in source})
        self.assertGreater(chunks[0][-1]["end"], chunks[1][0]["start"])

    def test_malformed_words_are_removed_and_ordered(self):
        words = _clean_words([{"start": 2, "end": 3, "text": "да"},
                              {"start": float("inf"), "end": 9, "text": "нет"},
                              {"start": 0, "end": 0, "text": "нет"},
                              {"start": 1, "end": 2, "word": "привет"}])
        self.assertEqual([w["text"] for w in words], ["привет", "да"])

    def test_rubric_weights_and_json_fences(self):
        rubric = {"rubric": {"hook": 10, "context": 0, "emotion": 0, "novelty": 0, "payoff": 0}}
        self.assertEqual(_score(rubric), 25)
        self.assertEqual(_json_payload("```json\n" + json.dumps({"candidates": []}) + "\n```"), {"candidates": []})
        with self.assertRaises(AIError):
            _json_payload("not valid json")


if __name__ == "__main__":
    unittest.main()
