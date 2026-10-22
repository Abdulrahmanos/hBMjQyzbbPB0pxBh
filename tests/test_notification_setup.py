"""Setup must never turn an unrelated Telegram message into a destination."""
import unittest
from tools.setup_telegram import matched_chat


class NotificationSetupTests(unittest.TestCase):
    def update(self, *, text="/start nonce", kind="private", author=123):
        return {"message": {"text": text, "chat": {"type": kind, "id": 123}, "from": {"id": author}}}

    def test_only_exact_challenge_in_owner_private_chat_matches(self):
        self.assertEqual(matched_chat(self.update(), "nonce"), 123)
        for update in ({}, self.update(text="/start"), self.update(text="/start other"),
                       self.update(kind="group"), self.update(author=999)):
            self.assertIsNone(matched_chat(update, "nonce"))


if __name__ == "__main__":
    unittest.main()
