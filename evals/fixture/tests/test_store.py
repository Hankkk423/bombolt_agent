import os
import tempfile
import unittest

from todo.store import NotFound, TodoStore


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TodoStore(os.path.join(self.tmp.name, "todos.json"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty(self):
        self.assertEqual(self.store.list(), [])

    def test_add_and_list(self):
        todo = self.store.add("買牛奶", "2026-10-20")
        self.assertEqual(todo["id"], 1)
        self.assertEqual(todo["due"], "2026-10-20")
        self.assertFalse(todo["done"])
        self.assertEqual([t["title"] for t in self.store.list()], ["買牛奶"])

    def test_ids_increase(self):
        self.store.add("a")
        self.store.add("b")
        self.assertEqual([t["id"] for t in self.store.list()], [1, 2])

    def test_empty_title_rejected(self):
        with self.assertRaises(ValueError):
            self.store.add("   ")

    def test_set_done(self):
        self.store.add("a")
        self.assertTrue(self.store.set_done(1)["done"])
        self.assertTrue(self.store.get(1)["done"])

    def test_delete(self):
        self.store.add("a")
        self.store.delete(1)
        self.assertEqual(self.store.list(), [])

    def test_missing_id(self):
        with self.assertRaises(NotFound):
            self.store.set_done(99)
        with self.assertRaises(NotFound):
            self.store.delete(99)


if __name__ == "__main__":
    unittest.main()
