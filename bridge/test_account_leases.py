"""Cross-process account ownership; every resource is a synthetic temporary path."""
import multiprocessing
import tempfile
import unittest
from pathlib import Path

from account_leases import AccountLeaseRegistry
from account_store import AccountConflict
from process_lock import ProcessLock, LockBusy


def hold_account(directory, account, ready, release):
    registry = AccountLeaseRegistry(lease_root=directory)
    with registry.hold(account):
        ready.set()
        release.wait(10)


class AccountLeaseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name) / "leases"
        self.registry = AccountLeaseRegistry(lease_root=self.directory)
        self.addCleanup(self.registry.close)

    def child(self):
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        process = context.Process(target=hold_account, args=(self.directory, "synthetic-account", ready, release))
        process.start()
        self.assertTrue(ready.wait(5))

        def cleanup():
            if process.is_alive():
                release.set()
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(3)
        self.addCleanup(cleanup)
        return process, release

    def test_another_process_cannot_read_or_clear_same_account(self):
        _process, release = self.child()
        with self.assertRaises(AccountConflict):
            self.registry.hold("synthetic-account")
        with self.registry.hold("other-synthetic-account"):
            pass
        release.set()

    def test_crash_releases_os_lock_without_deleting_a_pid_file(self):
        process, _release = self.child()
        process.terminate()
        process.join(3)
        with self.registry.hold("synthetic-account") as lease:
            lease.assert_held()

    def test_same_registry_clear_reentry_keeps_original_owner(self):
        original = self.registry.hold("synthetic-account")
        with self.registry.hold("synthetic-account"):
            pass
        competitor = AccountLeaseRegistry(lease_root=self.directory)
        with self.assertRaises(AccountConflict):
            competitor.hold("synthetic-account")
        original.release()
        with competitor.hold("synthetic-account"):
            pass

    def test_closed_registry_invalidates_issued_tokens(self):
        lease = self.registry.hold("synthetic-account")
        self.registry.close()
        with self.assertRaises(AccountConflict):
            lease.assert_held()
        lease.release()

    def test_empty_lock_file_needs_no_initial_write_while_peer_owns_it(self):
        path = self.directory / "synthetic-empty.lock"
        with ProcessLock(path):
            self.assertEqual(path.stat().st_size, 0)
            with self.assertRaises(LockBusy):
                ProcessLock(path).acquire()
        with ProcessLock(path):
            self.assertEqual(path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
