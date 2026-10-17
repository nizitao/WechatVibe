"""Exclusive ownership of the shared derived resources for one WeChat account."""
from __future__ import annotations

import threading
from pathlib import Path

from account_store import AccountConflict, _safe_account, account_id, default_snapshot_root
from process_lock import ProcessLock, LockBusy


class AccountLease:
    def __init__(self, registry, account, lock):
        self.registry, self.account, self.lock = registry, account, lock
        self.closed = False

    def assert_held(self):
        with self.registry.lock:
            if self.closed or self.lock.stream is None:
                raise AccountConflict("账号占用已失效，请重新选择账号")

    def release(self):
        with self.registry.lock:
            if self.closed:
                return
            self.closed = True
            entry = self.registry.held.get(self.account)
            if entry is not None and entry[0] is self.lock:
                entry[1] -= 1
                if entry[1] == 0:
                    self.registry.held.pop(self.account)
                    self.lock.release()

    def __enter__(self):
        self.assert_held()
        return self

    def __exit__(self, *_exc):
        self.release()


class AccountLeaseRegistry:
    def __init__(self, root=None, *, lease_root=None):
        # Snapshots are shared across installations as well as profiles. Do not
        # place this lock in an installation/profile's private runtime directory.
        self.directory = Path(lease_root) if lease_root is not None else default_snapshot_root() / ".wechatvibe-leases"
        self.lock = threading.RLock()
        self.held = {}

    def hold(self, account):
        if not _safe_account(account):
            raise AccountConflict("账号范围无效")
        with self.lock:
            entry = self.held.get(account)
            if entry is None:
                resource = ProcessLock(self.directory / (account_id(account) + ".lock"))
                try:
                    resource.acquire()
                except LockBusy as exc:
                    raise AccountConflict("此账号正由另一个窗口使用，请先关闭那个窗口") from exc
                entry = self.held[account] = [resource, 0]
            entry[1] += 1
            return AccountLease(self, account, entry[0])

    def close(self):
        with self.lock:
            for resource, _count in self.held.values():
                resource.release()
            self.held.clear()
