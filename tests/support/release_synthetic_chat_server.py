"""Use the actual v1.3.0 services with five authored chat conversations.

Staging replaces only bridge/chat_server.py with this entry, preserving the
original classifier as real_classifier.py. UI, models, assistant configuration,
scheduling and all request handlers remain the release's own implementation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def create_backend(root, classifier, *, analyzer=None):
    from backend_contracts import ROOT
    from backend_service import Backend
    from conversation_selection import ConversationSelectionStore
    from data_root_source import DataRootError
    from synthetic_fixtures import ACCOUNT, SyntheticDatabase, fixture_messages, plain_path, seed_fixtures, validate_root
    from wechat_source import WeChatSource

    root = validate_root(root)
    if root != plain_path(ROOT):
        raise RuntimeError("测试入口必须使用专用目录内的正式版服务")
    fixtures, _ = seed_fixtures(root)

    def factory(*, db_dir, account):
        if plain_path(db_dir) != fixtures or account != ACCOUNT:
            raise ValueError("测试数据范围以外的目录或账号")
        return SyntheticDatabase(root, account)

    class FixtureDataRoot:
        def status(self):
            return {"state": "ready", "path": str(fixtures), "exists": True, "accounts": 1}

        def select(self, _value):
            raise DataRootError("此窗口只使用合成测试会话，不能选择微信数据目录")

        def clear(self):
            return self.status()

    # Inject only the read adapter. In particular do not subclass Backend or
    # replace AdvisorService/model stores with implementations from later code.
    source = WeChatSource(factory=factory, classifier=classifier,
                          active_account_locator=lambda: fixtures / ACCOUNT,
                          media_factory=lambda *_args, **_kwargs: None)
    data = plain_path(root / ".local" / "real-client-data")
    selection = ConversationSelectionStore(data)
    if not selection.get(ACCOUNT)["initialized"]:
        selection.set_all_selected(ACCOUNT, list(fixture_messages()))
    return Backend(source, analyzer=analyzer, selection_store=selection,
                   data_root_store=FixtureDataRoot())


def main():
    bridge = str(Path(__file__).resolve().parent)
    if bridge not in sys.path:
        sys.path.insert(0, bridge)
    from account_api import AccountAPI
    from backend_contracts import ROOT
    from real_classifier import classify
    from real_http import CONTROL_TOKEN_ENV, ThreadingHTTPServer, default_port, integer, make_handler
    from synthetic_fixtures import ACCOUNT, SELF, validate_root

    root = validate_root(ROOT)
    # Keep the original services rooted here even when the parent shell carries
    # a real-account path hint. No process discovery or key extraction is used.
    os.environ.pop("WECHATVIBE_DATA_ROOT", None)
    os.environ["WECHATAUTO_KEYS_DIR"] = str(root / ".local" / "test-keys")
    token = os.environ.pop(CONTROL_TOKEN_ENV, None)
    backend = server = None
    try:
        backend = create_backend(root, classify)
        accounts = AccountAPI(backend, root / ".local" / "real-client-data",
                              snapshot_root=root / ".local" / "test-snapshots",
                              stable_keys_dir=root / ".local" / "test-keys")
        accounts.store.register(ACCOUNT, root / ".local" / "test-snapshots" / ACCOUNT,
                                SELF, "合成测试账号")
        port = integer(os.environ.get("CHATUI_PORT"), default_port(root), 65535)
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(backend, accounts, token))
        print(f"chatui server on http://127.0.0.1:{port}", flush=True)
        server.serve_forever()
    finally:
        if server is not None:
            server.server_close()
        if backend is not None:
            backend.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
