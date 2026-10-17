"""Isolated acceptance data for the staged test client; never discovers WeChat.

This support module is copied into .local/test-client/client/bridge. It is not
part of a production client or an alternative model: inference still uses the
normal Backend and NodeAnalysis implementation.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from types import SimpleNamespace

from account_pin_source import AccountPinSource
from account_leases import AccountLeaseRegistry
from backend_contracts import QUOTED_REPLY_TYPE
from backend_service import Backend
from conversation_selection import ConversationSelectionStore
from data_root_source import DataRootError
from model_source import ModelSourceStore
from wechat_source import WeChatSource

MARKER = ".synthetic-client.json"
ACCOUNTS = ("fixture-account-a", "fixture-account-b")
_discovery_root = None
CONTACTS = {
    "fixture-self": "测试账号本人",
    "fixture-lin": "林晓 · 合成邀约",
    "fixture-chen": "陈安 · 合成日常",
    "fixture-qiao": "乔宁 · 合成长对话",
    "fixture-yan": "言希 · 合成否定引用",
    "fixture-study@chatroom": "周末计划 · 合成群聊",
}


def plain_path(path):
    path = Path(os.path.abspath(path))
    for current in (path, *path.parents):
        try:
            stat = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("测试客户端路径不可为符号链接或目录联接")
    return path


def validate_root(root):
    root = plain_path(root)
    marker = plain_path(root / MARKER)
    if not marker.is_file() or marker.stat().st_size > 8192:
        raise RuntimeError("缺少测试客户端隔离标记，已拒绝启动")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("测试客户端隔离标记不可用") from exc
    if (not isinstance(data, dict) or data.get("schema") != 1 or
            data.get("mode") != "synthetic" or not isinstance(data.get("sourceRoot"), str)):
        raise RuntimeError("测试客户端隔离标记不可用")
    if not Path(data["sourceRoot"]).is_absolute():
        raise RuntimeError("测试客户端源码标记必须为绝对路径")
    source = plain_path(data["sourceRoot"])
    if root != source / ".local" / "test-client" / "client":
        raise RuntimeError("测试客户端不在专用隔离目录，已拒绝启动")
    return root


def fixture_messages(account):
    """All lines are authored examples, not exports or generated model answers."""
    own = "fixture-self"
    def pairs(user, turns):
        return [(own if index % 2 == 0 else user, text, 1)
                for index, text in enumerate(turns)]
    rows = {
        "fixture-lin": pairs("fixture-lin", [
            "晚上一起去吃饭吗？", "今天有点累，改天吧[微笑]。",
            "没关系，你先休息。", "谢谢你理解，等我缓过来再约。",
            "那周六下午呢？", "周六可以，三点在书店见怎么样？",
            "好，我提前订位置。", "别订太贵的，简单吃点就好。",
            "你是不是不想去呀？", "不是不想去，我只是最近预算有点紧。",
            "明白，那散步也行。", "这个主意不错，我很期待。",
        ]),
        "fixture-chen": pairs("fixture-chen", [
            "我到了。", "好，我在楼下。", "带水了吗？", "带了两瓶。",
            "我们走东门吧。", "行，东门见。", "今天路上好堵。", "慢点来，不着急。",
            "刚才忘了回你。", "没事，我也在忙。",
        ]),
        "fixture-yan": pairs("fixture-yan", [
            "你说的那个安排还有效吗？", "有效，我没有取消，只是把时间改到了明天。",
            "我以为你生气了。", "我不是生你的气，是觉得沟通有点累。",
            "小李说他讨厌聚会。", "那是小李的想法，我其实愿意参加。",
            "所以你总是喜欢一个人吗？", "不是，我也很喜欢和熟悉的人一起出去。",
            "这次你来决定吧。", "让我再想想，我暂时没有把握。",
        ]),
    }
    quote = ("<msg><appmsg><title>我没有说不去，只是需要晚一点。</title>"
             "<refermsg><content>你是不是又不去了？</content></refermsg></appmsg></msg>")
    rows["fixture-yan"].extend([(own, "你是不是又不去了？", 1),
                                ("fixture-yan", quote, QUOTED_REPLY_TYPE)])
    situations = [
        ("新活动要开始了，你打算怎么参与？", "我想先看看具体安排，再决定负责哪一块。"),
        ("这次时间比较紧，你有什么建议？", "先列出必须完成的事情，其余可以留到下次。"),
        ("要不要叫大家一起讨论？", "可以，大家把想法写下来，我们再一起比较。"),
        ("你愿意试一试新的路线吗？", "愿意，只要提前确认交通和返程时间。"),
        ("临时改计划你会不舒服吗？", "会有一点，但了解原因之后可以一起调整。"),
        ("你更在意方案的什么部分？", "我在意实际能不能完成，也想听听大家的感受。"),
        ("今天还继续聊吗？", "我有点累了，想先休息，明天再接着说。"),
        ("刚才的反馈让你难受了吗？", "有一点，不过具体建议很有用，我会认真改。"),
        ("这个任务交给你会不会太多？", "有点多，可以帮我分担最后的整理吗？"),
        ("结果比预期好！", "太好了，大家一起努力有了成果。"),
        ("你觉得他的反应是什么意思？", "暂时还说不准，最好问清楚，别替他下结论。"),
        ("这个周末有什么安排？", "上午自己读书，下午约朋友散步，晚上再整理笔记。"),
    ]
    long_rows = []
    for day in range(1, 11):
        for index, (question, answer) in enumerate(situations):
            long_rows.extend([(own, f"第{day}次活动，{question}", 1),
                              ("fixture-qiao", answer if day % 2 else f"我的想法是：{answer}", 1)])
    rows["fixture-qiao"] = long_rows  # 120 target statements, no fabricated personality label.
    group = [
        (own, "这个周末大家有什么安排？", 1),
        ("fixture-lin", "我想出去走走，最近闷坏了。", 1),
        ("fixture-chen", "我都行，不过周日上午要上课。", 1),
        ("fixture-qiao", "那就下午见，先确认一下天气。", 1),
        ("fixture-yan", "我这次不去了，需要在家休息。", 1),
        (own, "好，你安心休息，不勉强。", 1),
        ("fixture-yan", "谢谢，之后再一起玩。", 1),
        ("fixture-lin", "我可以带饮料，谁需要无糖的？", 1),
        ("fixture-chen", "给我一瓶无糖的，谢谢！", 1),
        ("fixture-qiao", "我负责查路线，晚点发安排。", 1),
    ]
    rows["fixture-study@chatroom"] = group
    if account == ACCOUNTS[1]:
        for user in rows:
            rows[user] = [(own, "这里是合成测试账号 B，与账号 A 数据隔离。", 1), *rows[user]]
    return rows


def _create_database(path, build):
    path = plain_path(path)
    if path.exists():
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("合成测试数据库损坏")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with closing(sqlite3.connect(pending)) as conn, conn:
            build(conn)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def seed_fixtures(root):
    root = validate_root(root)
    snapshots = plain_path(root / ".local" / "test-snapshots")
    fixture_root = plain_path(root / ".local" / "test-fixtures")
    for account in ACCOUNTS:
        rows = fixture_messages(account)
        account_dir = fixture_root / account
        plain_path(account_dir / "db_storage").mkdir(parents=True, exist_ok=True)
        workdir = snapshots / account
        def contacts(conn):
            conn.execute("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,small_head_url TEXT,big_head_url TEXT)")
            conn.executemany("INSERT INTO contact VALUES(?,?,?,?,?)", [
                (user, ("合成测试账号 " + account[-1].upper()) if user == "fixture-self" else name, "", "", "")
                for user, name in CONTACTS.items()])
        def sessions(conn):
            conn.execute("CREATE TABLE SessionTable(username TEXT,unread_count INT,summary TEXT,last_timestamp INT,"
                         "last_msg_sender TEXT,last_sender_display_name TEXT,sort_timestamp INT,is_hidden INT,"
                         "last_msg_type INT,last_msg_sub_type INT,is_top INT)")
            for index, (user, messages) in enumerate(rows.items()):
                sender, text, kind = messages[-1]
                conn.execute("INSERT INTO SessionTable VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    user, 0, text if kind == 1 else "[引用消息]", 1791417600 + index * 60,
                    sender, CONTACTS[sender], index + 1, 0, kind, 0, int(index == 0)))
        def messages(conn):
            conn.execute("CREATE TABLE Name2Id(user_name TEXT)")
            sender_ids = {user: index + 1 for index, user in enumerate(CONTACTS)}
            conn.executemany("INSERT INTO Name2Id(rowid,user_name) VALUES(?,?)", [(value, user) for user, value in sender_ids.items()])
            for user, items in rows.items():
                table = "Msg_" + hashlib.md5(user.encode()).hexdigest()
                conn.execute(f"CREATE TABLE {table}(local_id INT,local_type INT,real_sender_id INT,create_time INT,"
                             "message_content TEXT,compress_content BLOB,server_id INT,sort_seq INT)")
                conn.executemany(f"INSERT INTO {table} VALUES(?,?,?,?,?,?,?,?)", [
                    (index, kind, sender_ids[sender], 1791417600 + index * 60, text, None, index, index)
                    for index, (sender, text, kind) in enumerate(items, 1)])
                conn.execute(f"CREATE INDEX idx_{table}_order ON {table}(sort_seq,local_id)")
        _create_database(workdir / "contact__contact.db", contacts)
        _create_database(workdir / "session__session.db", sessions)
        _create_database(workdir / "message__message_0.db", messages)
    return fixture_root, snapshots


class SyntheticDatabase:
    def __init__(self, root, account):
        if account not in ACCOUNTS:
            raise ValueError("测试账号不存在")
        self.root, self.account = validate_root(root), account
        self.wxid = "fixture-self"
        self.account_dir = self.root / ".local" / "test-fixtures" / account
        self.workdir = self.root / ".local" / "test-snapshots" / account
        self.files = {"contact/contact.db": self.workdir / "contact__contact.db",
                      "session/session.db": self.workdir / "session__session.db",
                      "message/message_0.db": self.workdir / "message__message_0.db"}
        self._db_files = [(rel, (str(path) if rel.startswith("message/") else rel), path.stat().st_size)
                          for rel, path in self.files.items()]
        self.messages_ready = True

    def _open(self, rel):
        if rel not in self.files:
            raise ValueError("测试数据范围以外的数据库")
        path = plain_path(self.files[rel])
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)

    def _msg_conns(self, username):
        if username not in fixture_messages(self.account):
            return []
        return [(self._open("message/message_0.db"), "Msg_" + hashlib.md5(username.encode()).hexdigest())]

    def _message_dbs(self):
        return ["message/message_0.db"]

    def get_self_info(self):
        return {"username": self.wxid}

    @staticmethod
    def _msg_type_name(kind):
        return "文本" if kind == 1 else "引用消息" if kind == QUOTED_REPLY_TYPE else "消息"

    @staticmethod
    def _friendly_content(value, _kind):
        return value.decode("utf-8") if isinstance(value, bytes) else value


@contextmanager
def fixture_discovery(root):
    """Substitute discovered inputs, preserving the real account-pin decisions.

    The production pin's persistence, select/clear, status and locator all run
    unchanged. Only the platform discovery results are authored fixtures. The
    boundary lives for the whole server lifetime, including shutdown, so a
    background task cannot fall back to a real WeChat account.
    """
    global _discovery_root
    root = validate_root(root)
    if _discovery_root is not None:
        raise RuntimeError("合成账号发现边界已经启用")
    import live_source
    from wr import discovery

    directories = {account: root / ".local" / "test-fixtures" / account for account in ACCOUNTS}

    def selections(*, fresh=True):
        validate_root(root)
        return [live_source.ActiveSelection(plain_path(directory), ()) for directory in directories.values()]

    def active(*, fresh=True):
        found = selections(fresh=fresh)
        return found[0] if len(found) == 1 else None

    def selected(account_dir, *, fresh=True):
        if not isinstance(account_dir, (str, Path)) or not Path(account_dir).is_absolute():
            return None
        candidate = plain_path(account_dir)
        return next((item for item in selections(fresh=fresh) if item.account_dir == candidate), None)

    def discovered():
        validate_root(root)
        return [SimpleNamespace(id=account, path=plain_path(directory / "db_storage"))
                for account, directory in directories.items()]

    replacements = [(live_source, "live_account_selections", selections),
                    (live_source, "active_account_snapshot", active),
                    (live_source, "selection_for_account", selected),
                    (discovery, "discover_account_dirs", discovered),
                    (discovery, "find_weixin_processes", lambda: [])]
    originals = [(module, name, getattr(module, name)) for module, name, _ in replacements]
    try:
        for module, name, value in replacements:
            setattr(module, name, value)
        _discovery_root = root
        yield
    finally:
        _discovery_root = None
        for module, name, value in reversed(originals):
            setattr(module, name, value)


class SyntheticDataRoot:
    def __init__(self, root):
        self.root = validate_root(root)

    def status(self):
        return {"state": "ready", "path": str(self.root / ".local" / "test-fixtures"),
                "exists": True, "accounts": len(ACCOUNTS), "synthetic": True}

    def select(self, _value):
        raise DataRootError("测试版固定使用合成数据，不能读取微信数据目录")

    def clear(self):
        return self.status()


def create_backend(root, classifier, *, analyzer=None, profile=None):
    root = validate_root(root)
    if _discovery_root != root:
        raise RuntimeError("创建服务前必须启用合成账号发现边界")
    seed_fixtures(root)
    from instance_identity import runtime_dir
    directory = runtime_dir(root, profile)
    pin = AccountPinSource(root, profile)
    # The first launch opens account A. Later explicit pins, including cleared
    # pins, retain exactly the production behavior rather than being reset.
    if not pin.config.exists():
        pin.select(str(root / ".local" / "test-fixtures" / ACCOUNTS[0]))

    def factory(*, db_dir, account):
        if plain_path(db_dir) != root / ".local" / "test-fixtures":
            raise ValueError("测试数据范围以外的目录")
        return SyntheticDatabase(root, account)

    account_locator = pin.locator()

    def locate(*, fresh=True):
        # An injected database factory takes a directory; the platform reader
        # takes an ActiveSelection with process identities. Keep pin resolution
        # real and adapt only that input representation for the fixture reader.
        selection = account_locator(fresh=fresh)
        return selection.account_dir if selection is not None else None

    source = WeChatSource(factory=factory, classifier=classifier,
                          active_account_locator=locate,
                          media_factory=lambda *_args, **_kwargs: None)
    source.account_leases = AccountLeaseRegistry(lease_root=root / ".local" / "test-snapshots" / ".leases")
    data = root / ".local" / "real-client-data"
    data.mkdir(parents=True, exist_ok=True)
    selection = ConversationSelectionStore(data)
    for account in ACCOUNTS:
        if not selection.get(account)["initialized"]:
            selection.set_all_selected(account, list(fixture_messages(account)))
    return Backend(source, analyzer=analyzer,
                            model_source_store=ModelSourceStore(directory / "api-model-source.json", root=root,
                                                                 legacy_path=directory / "model-source.json"),
                            selection_store=selection,
                            data_root_store=SyntheticDataRoot(root), account_pin_store=pin)


def main(classifier):
    from backend_contracts import ROOT
    from account_api import AccountAPI
    from instance_identity import profile_name, runtime_dir, default_port
    from installation_gate import InstallationGate
    from real_http import CONTROL_TOKEN_ENV, make_handler, integer, ThreadingHTTPServer
    root = validate_root(ROOT)
    # An inherited production snapshot/key hint must never leak into deletion,
    # diagnostics or any later helper invoked from this isolated process.
    os.environ.pop("WECHATVIBE_DATA_ROOT", None)
    os.environ.pop("WECHATVIBE_ACCOUNT_PIN", None)
    os.environ["WECHATAUTO_KEYS_DIR"] = str(root / ".local" / "test-keys")
    with fixture_discovery(root):
        gate = InstallationGate(root)
        backend = server = None
        try:
            gate.register(role="bridge")
            profile = profile_name()
            backend = create_backend(root, classifier, profile=profile)
            accounts = AccountAPI(backend, root / ".local" / "real-client-data",
                                  snapshot_root=root / ".local" / "test-snapshots",
                                  stable_keys_dir=root / ".local" / "test-keys",
                                  runtime_dir=runtime_dir(root, profile))
            for account in ACCOUNTS:
                accounts.store.register(account, root / ".local" / "test-snapshots" / account,
                                        "fixture-self", "合成测试账号 " + account[-1].upper())
            token = os.environ.pop(CONTROL_TOKEN_ENV, None)
            port = integer(os.environ.get("CHATUI_PORT"), default_port(root, profile), 65535)
            server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(backend, accounts, token, profile))
            print(f"synthetic chatui server on http://127.0.0.1:{port}", flush=True)
            server.serve_forever()
        finally:
            if server is not None:
                server.server_close()
            if backend is not None:
                backend.shutdown()
            gate.close()
    return 0
