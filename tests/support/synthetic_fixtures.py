"""Authored chat fixtures for the unmodified v1.3.0 backend.

Only the small database adapter is synthetic. No model responses, assistant
catalogs, credentials or personal chat records are supplied by this module.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

RELEASE_COMMIT = "1e9e9b9f329e49c90f7a99d9a588268ae0f74c86"
MARKER = ".synthetic-client.json"
ACCOUNT = "fixture-account-a"
SELF = "fixture-self"
QUOTED_REPLY_TYPE = (57 << 32) | 49
CONTACTS = {
    SELF: "合成测试账号",
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
            details = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or getattr(details, "st_file_attributes", 0) & 0x400:
            raise RuntimeError("测试数据路径不可为符号链接或目录联接")
    return path


def validate_root(root):
    root = plain_path(root)
    marker = plain_path(root / MARKER)
    if not marker.is_file() or marker.stat().st_size > 8192:
        raise RuntimeError("缺少正式版本测试目录标记")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError("正式版本测试目录标记不可用") from exc
    if (not isinstance(data, dict) or data.get("schema") != 1 or
            data.get("mode") != "synthetic" or data.get("releaseCommit") != RELEASE_COMMIT or
            not isinstance(data.get("sourceRoot"), str) or not Path(data["sourceRoot"]).is_absolute()):
        raise RuntimeError("正式版本测试目录标记不匹配")
    source = plain_path(data["sourceRoot"])
    if root != source / ".local" / "test-client-v1.3.0" / "client":
        raise RuntimeError("测试数据不在正式版本专用目录内")
    return root


def fixture_messages():
    """The same hand-authored examples, without a second synthetic account."""
    def pairs(user, turns):
        return [(SELF if index % 2 == 0 else user, text, 1) for index, text in enumerate(turns)]

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
    rows["fixture-yan"].extend([(SELF, "你是不是又不去了？", 1),
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
    rows["fixture-qiao"] = []
    for day in range(1, 11):
        for question, answer in situations:
            rows["fixture-qiao"].extend([
                (SELF, f"第{day}次活动，{question}", 1),
                ("fixture-qiao", answer if day % 2 else f"我的想法是：{answer}", 1),
            ])
    rows["fixture-study@chatroom"] = [
        (SELF, "这个周末大家有什么安排？", 1),
        ("fixture-lin", "我想出去走走，最近闷坏了。", 1),
        ("fixture-chen", "我都行，不过周日上午要上课。", 1),
        ("fixture-qiao", "那就下午见，先确认一下天气。", 1),
        ("fixture-yan", "我这次不去了，需要在家休息。", 1),
        (SELF, "好，你安心休息，不勉强。", 1),
        ("fixture-yan", "谢谢，之后再一起玩。", 1),
        ("fixture-lin", "我可以带饮料，谁需要无糖的？", 1),
        ("fixture-chen", "给我一瓶无糖的，谢谢！", 1),
        ("fixture-qiao", "我负责查路线，晚点发安排。", 1),
    ]
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
    fixtures = plain_path(root / ".local" / "test-fixtures")
    snapshots = plain_path(root / ".local" / "test-snapshots")
    plain_path(fixtures / ACCOUNT / "db_storage").mkdir(parents=True, exist_ok=True)
    workdir = snapshots / ACCOUNT
    rows = fixture_messages()

    def contacts(conn):
        conn.execute("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,small_head_url TEXT,big_head_url TEXT)")
        conn.executemany("INSERT INTO contact VALUES(?,?,?,?,?)", [
            (user, name, "", "", "") for user, name in CONTACTS.items()])

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
    return fixtures, snapshots


class SyntheticDatabase:
    """The v1.3.0 WeChatSource reader interface, backed only by our fixed fixture."""
    def __init__(self, root, account):
        if account != ACCOUNT:
            raise ValueError("测试账号不存在")
        self.root, self.account = validate_root(root), ACCOUNT
        self.wxid = SELF
        self.account_dir = self.root / ".local" / "test-fixtures" / ACCOUNT
        self.workdir = self.root / ".local" / "test-snapshots" / ACCOUNT
        self.files = {"contact/contact.db": self.workdir / "contact__contact.db",
                      "session/session.db": self.workdir / "session__session.db",
                      "message/message_0.db": self.workdir / "message__message_0.db"}
        self._db_files = [(rel, str(path) if rel.startswith("message/") else rel, path.stat().st_size)
                          for rel, path in self.files.items()]
        self.messages_ready = True

    def _open(self, relative):
        if relative not in self.files:
            raise ValueError("测试数据范围以外的数据库")
        path = plain_path(self.files[relative])
        return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)

    def _msg_conns(self, username):
        if username not in fixture_messages():
            return []
        return [(self._open("message/message_0.db"), "Msg_" + hashlib.md5(username.encode()).hexdigest())]

    def _message_dbs(self):
        return ["message/message_0.db"]

    def get_self_info(self):
        return {"username": SELF}

    @staticmethod
    def _msg_type_name(kind):
        return "文本" if kind == 1 else "引用消息" if kind == QUOTED_REPLY_TYPE else "消息"

    @staticmethod
    def _friendly_content(value, _kind):
        return value.decode("utf-8") if isinstance(value, bytes) else value
