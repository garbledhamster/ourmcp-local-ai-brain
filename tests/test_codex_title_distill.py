import gc
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from local_ai_brain import title_distill as codex_title_distill


def create_state(codex_home: Path, thread_id: str, title: str, first_user_message: str, final_text: str) -> None:
    sessions = codex_home / "sessions"
    sessions.mkdir(parents=True)
    rollout = sessions / f"{thread_id}.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final",
                    "content": [{"type": "output_text", "text": final_text}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    db = codex_home / "state_5.sqlite"
    con = sqlite3.connect(db)
    con.execute(
        """
        create table threads (
            id text primary key,
            title text not null,
            created_at integer not null,
            rollout_path text not null,
            first_user_message text not null default '',
            thread_source text,
            agent_nickname text
        )
        """
    )
    con.execute(
        """
        insert into threads
        (id, title, created_at, rollout_path, first_user_message, thread_source, agent_nickname)
        values (?, ?, ?, ?, ?, 'user', null)
        """,
        (thread_id, title, 1780655437, str(rollout), first_user_message),
    )
    con.commit()
    con.close()
    (codex_home / "session_index.jsonl").write_text("", encoding="utf-8")


class CodexTitleDistillTests(unittest.TestCase):
    def temp_home(self):
        return tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def test_dry_run_does_not_mutate_db(self):
        with self.temp_home() as tmp:
            home = Path(tmp)
            create_state(
                home,
                "thread-1",
                "raw prompt title",
                "build installer",
                "Implemented the universal Python installer. Verification passed.",
            )

            code = codex_title_distill.main(["--codex-home", str(home), "--date", "2026-06-05"])

            self.assertEqual(code, 0)
            con = sqlite3.connect(home / "state_5.sqlite")
            title = con.execute("select title from threads where id='thread-1'").fetchone()[0]
            con.close()
            self.assertEqual(title, "raw prompt title")
            self.assertTrue((home / "title-distill-audit").exists())
            gc.collect()

    def test_apply_updates_title_and_appends_index(self):
        with self.temp_home() as tmp:
            home = Path(tmp)
            create_state(
                home,
                "thread-2",
                "ourstuff.space remove the daily return element from the site and make sure it does not leave layout gaps",
                "remove daily return",
                "Removed the Daily Return element. Verified desktop and mobile screenshots.",
            )

            code = codex_title_distill.main(
                ["--codex-home", str(home), "--date", "2026-06-05", "--apply", "--yes"]
            )

            self.assertEqual(code, 0)
            con = sqlite3.connect(home / "state_5.sqlite")
            title = con.execute("select title from threads where id='thread-2'").fetchone()[0]
            con.close()
            self.assertEqual(title, "2026-06-05 Removed Daily Return Element")
            self.assertTrue(list(home.glob("state_5.sqlite.bak-codex-title-distill-*")))
            index_text = (home / "session_index.jsonl").read_text(encoding="utf-8")
            self.assertIn("2026-06-05 Removed Daily Return Element", index_text)
            gc.collect()

    def test_existing_date_prefix_is_skipped_without_force(self):
        with self.temp_home() as tmp:
            home = Path(tmp)
            create_state(
                home,
                "thread-3",
                "2026-06-05 Existing Title",
                "fix reader",
                "Fixed the reader horizontal overflow.",
            )

            proposals = codex_title_distill.make_proposals(
                codex_title_distill.read_threads(home / "state_5.sqlite", "2026-06-05", None),
                "2026-06-05",
                force_retitle=False,
                max_words=7,
            )

            self.assertTrue(proposals[0].skipped)
            self.assertEqual(proposals[0].new_title, "2026-06-05 Existing Title")
            gc.collect()

    def test_force_retitle_preserves_existing_date_title(self):
        with self.temp_home() as tmp:
            home = Path(tmp)
            create_state(
                home,
                "thread-4",
                "2026-06-05 Existing Strong Title",
                "fix reader",
                "Found none of the expected result markers.",
            )

            proposals = codex_title_distill.make_proposals(
                codex_title_distill.read_threads(home / "state_5.sqlite", "2026-06-05", None),
                "2026-06-05",
                force_retitle=True,
                max_words=7,
            )

            self.assertFalse(proposals[0].skipped)
            self.assertEqual(proposals[0].new_title, "2026-06-05 Existing Strong Title")
            self.assertEqual(proposals[0].source, "existing date title")
            gc.collect()


if __name__ == "__main__":
    unittest.main()
