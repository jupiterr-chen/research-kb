import os
import unittest

from fixtures import make_config, temp_dir

from library.ingest import Ingestor
from library.locking import FileLock


class LockingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = temp_dir()
        self.config, self.reports, self.discord = make_config(self.tmp)

    def test_exclusive_lock_blocks_second_holder(self):
        path = os.path.join(self.config.state_dir, "ingest.lock")
        first = FileLock(path)
        second = FileLock(path)
        self.assertTrue(first.acquire(blocking=False))
        try:
            self.assertFalse(second.acquire(blocking=False))
        finally:
            first.release()
        self.assertTrue(second.acquire(blocking=False))
        second.release()

    def test_lock_released_after_exception(self):
        path = os.path.join(self.config.state_dir, "ingest.lock")
        try:
            with FileLock(path):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        again = FileLock(path)
        self.assertTrue(again.acquire(blocking=False))
        again.release()

    def test_ingest_skips_when_lock_held(self):
        ingestor = Ingestor(self.config)
        holder = FileLock(ingestor.lock_path())
        self.assertTrue(holder.acquire(blocking=False))
        try:
            result = ingestor.run()
            self.assertTrue(result.get("skipped"))
        finally:
            holder.release()
        # after release, a normal run proceeds
        try:
            result = ingestor.run()
            self.assertTrue(result.get("ok"))
        finally:
            ingestor.close()

    def test_lock_released_when_source_scan_fails(self):
        # Missing index makes the discord scan raise; the lock must be freed.
        self.config.sources["discord"].extra["index"] = os.path.join(self.tmp, "missing.jsonl")
        ingestor = Ingestor(self.config)
        try:
            result = ingestor.run()
            self.assertFalse(result.get("ok"))
        finally:
            ingestor.close()
        again = FileLock(os.path.join(self.config.state_dir, "ingest.lock"))
        self.assertTrue(again.acquire(blocking=False))
        again.release()


if __name__ == "__main__":
    unittest.main()
