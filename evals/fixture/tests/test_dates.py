import unittest

from todo.dates import is_overdue, parse_due


class ParseDueTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_due("2026-10-20"), "2026-10-20")

    def test_empty_is_none(self):
        self.assertIsNone(parse_due(""))
        self.assertIsNone(parse_due(None))

    def test_invalid(self):
        with self.assertRaises(ValueError):
            parse_due("2026/10/20")


class IsOverdueTest(unittest.TestCase):
    def test_past_due_is_overdue(self):
        self.assertTrue(is_overdue({"done": False, "due": "2026-10-01"}, "2026-10-14"))

    def test_future_is_not_overdue(self):
        self.assertFalse(is_overdue({"done": False, "due": "2026-10-20"}, "2026-10-14"))

    def test_done_is_never_overdue(self):
        self.assertFalse(is_overdue({"done": True, "due": "2026-10-01"}, "2026-10-14"))

    def test_no_due(self):
        self.assertFalse(is_overdue({"done": False, "due": None}, "2026-10-14"))


if __name__ == "__main__":
    unittest.main()
