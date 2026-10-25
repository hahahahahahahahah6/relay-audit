import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from relay_audit.estimator import estimate_tokens


class EstimatorTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(estimate_tokens(''), 0)

    def test_whitespace(self):
        self.assertEqual(estimate_tokens(' \n\t'), 0)

    def test_latin_runs(self):
        self.assertEqual(estimate_tokens('hello world'), 4)

    def test_rounding(self):
        self.assertEqual([estimate_tokens('a' * n) for n in (1, 4, 5, 8, 9)], [1, 1, 2, 2, 3])

    def test_cjk(self):
        self.assertEqual(estimate_tokens('你好世界'), 4)
        self.assertEqual(estimate_tokens('かな한글'), 4)

    def test_mixed(self):
        self.assertEqual(estimate_tokens('hello你好world'), 6)

    def test_deterministic(self):
        self.assertEqual(len({estimate_tokens('same synthetic 文本') for _ in range(100)}), 1)

    def test_prefix_monotonicity(self):
        text = 'hello world 你好 testing\tmore'
        values = [estimate_tokens(text[:n]) for n in range(len(text) + 1)]
        self.assertEqual(values, sorted(values))
