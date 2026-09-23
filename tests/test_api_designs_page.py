"""GET /designs/page 只读 keyset 分页接口的集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_designs_page.py
（也可被 pytest 收集）。
"""

import os
import sys
import tempfile

_TMP_DB = tempfile.NamedTemporaryFile(prefix="weave-test-", suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DATABASE_PATH"] = _TMP_DB.name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok: {msg}")


TWILL = {
    "name": "2/2 twill",
    "cyclic": True,
    "max_float": 2,
    "threading": [0, 1, 2, 3, 0, 1, 2, 3],
    "tie_up": [
        [1, 1, 0, 0],
        [0, 1, 1, 0],
        [0, 0, 1, 1],
        [1, 0, 0, 1],
    ],
    "treadling": [0, 1, 2, 3, 0, 1, 2, 3],
}

SUMMARY_KEYS = {
    "id",
    "name",
    "cyclic",
    "max_float",
    "shafts",
    "treadles",
    "warp_ends",
    "weft_picks",
    "exceeds",
    "created_at",
}


def _walk_pages(client, limit, after_id=None):
    """按 next_after_id 从给定游标开始遍历到底，收集全部摘要与游标序列。"""
    seen = []
    cursors = []
    while True:
        params = {"limit": limit}
        if after_id is not None:
            params["after_id"] = after_id
        data = client.get("/designs/page", params=params).json()
        check(
            [i["id"] for i in data["items"]]
            == sorted(i["id"] for i in data["items"]),
            "page items are ascending by id",
        )
        seen.extend(data["items"])
        if not data["has_more"]:
            check(
                data["next_after_id"] is None,
                "last page reports next_after_id null",
            )
            return seen, cursors
        check(
            data["next_after_id"] == data["items"][-1]["id"],
            "next_after_id is the last returned id",
        )
        cursors.append(data["next_after_id"])
        after_id = data["next_after_id"]


def test_empty_page():
    with TestClient(app) as client:
        resp = client.get("/designs/page", params={"limit": 20})
        check(resp.status_code == 200, "empty db page returns 200")
        data = resp.json()
        check(data["items"] == [], "empty db yields no items")
        check(data["next_after_id"] is None, "empty db next_after_id null")
        check(data["has_more"] is False, "empty db has_more false")


def test_keyset_pagination_walks_all_once():
    with TestClient(app) as client:
        baseline = [d["id"] for d in client.get("/designs").json()]
        created = [client.post("/designs", json=TWILL).json()["id"] for _ in range(5)]
        expected = baseline + created
        resp = client.get("/designs/page", params={"limit": 2})
        check(resp.status_code == 200, "first page returns 200")
        first = resp.json()
        check([i["id"] for i in first["items"]] == expected[:2],
              "first page returns the two smallest ids")
        check(first["has_more"] is True, "first page signals more")
        check(first["next_after_id"] == expected[1],
              "first page cursor is its last id")

        items, _ = _walk_pages(client, 2)
        ids = [i["id"] for i in items]
        check(ids == expected, "whole walk covers exactly all designs in id order")
        check(len(ids) == len(set(ids)), "no id repeats across pages")

        # 每页恰好满时的边界：limit 整除总数 -> 最后一页满且 has_more=false
        items5, _ = _walk_pages(client, 5)
        check([i["id"] for i in items5] == expected,
              "page size equal to total returns one page each")

        # limit 大于总数：单页、无后续
        data = client.get("/designs/page", params={"limit": 100}).json()
        check([i["id"] for i in data["items"]] == expected,
              "limit above total yields one page")
        check(data["has_more"] is False and data["next_after_id"] is None,
              "oversized page has no next page")

        # 摘要字段与 GET /designs 一致，且不含 threading/tie_up/audit 等重字段
        new_items = [i for i in items if i["id"] in set(created)]
        item = new_items[0]
        check(set(item.keys()) == SUMMARY_KEYS, "item is a lightweight summary")
        listing = {d["id"]: d for d in client.get("/designs").json()}
        check(item == listing[item["id"]], "summary matches GET /designs shape")


def test_after_id_is_exclusive():
    with TestClient(app) as client:
        created = [client.post("/designs", json=TWILL).json()["id"] for _ in range(3)]
        data = client.get(
            "/designs/page", params={"limit": 10, "after_id": created[0]}
        ).json()
        check([i["id"] for i in data["items"]] == created[1:],
              "after_id is exclusive: only ids strictly greater than cursor")

        # 游标超过当前最大 ID：空页而非报错
        data = client.get(
            "/designs/page", params={"limit": 10, "after_id": 999_999_999}
        ).json()
        check(data["items"] == [] and data["has_more"] is False,
              "cursor beyond max id yields empty last page")


def test_inserts_during_walk_do_not_duplicate():
    with TestClient(app) as client:
        baseline = [d["id"] for d in client.get("/designs").json()]
        created = [client.post("/designs", json=TWILL).json()["id"] for _ in range(5)]
        expected_existing = baseline + created

        # 取走第一页后、继续遍历前新增一个设计（ID 更大）
        first = client.get("/designs/page", params={"limit": 2}).json()
        seen = [i["id"] for i in first["items"]]
        new_id = client.post("/designs", json=TWILL).json()["id"]
        check(new_id > expected_existing[-1], "new design gets a strictly greater id")

        cursor = first["next_after_id"]
        while True:
            data = client.get(
                "/designs/page", params={"limit": 2, "after_id": cursor}
            ).json()
            seen.extend(i["id"] for i in data["items"])
            if not data["has_more"]:
                break
            cursor = data["next_after_id"]

        check(len(seen) == len(set(seen)), "no already-returned id repeats")
        check(seen.count(new_id) == 1, "the new design appears exactly once")
        check([i for i in seen if i != new_id] == expected_existing,
              "all pre-existing designs still covered exactly once")


def test_invalid_params_return_400():
    with TestClient(app) as client:
        bad_queries = [
            "",                       # limit 缺失
            "limit=0",                # 小于 1
            "limit=-1",
            "limit=101",              # 超过 100
            "limit=abc",              # 非整数
            "limit=1.5",
            "limit=2&after_id=0",     # after_id 非正
            "limit=2&after_id=-3",
            "limit=2&after_id=1.0",   # after_id 非整数
            "limit=2&after_id=abc",
            "limit=2&after_id=",      # after_id 空串
        ]
        for query in bad_queries:
            resp = client.get(f"/designs/page?{query}")
            check(resp.status_code == 400,
                  f"invalid query {query!r} returns 400 (got {resp.status_code})")
            check("detail" in resp.json(), "400 carries a detail message")

        # 合法边界值：1 与 100、带正号 / 前导零的十进制整数均通过
        for value in ("1", "100", "+2", "02"):
            resp = client.get(f"/designs/page?limit={value}")
            check(resp.status_code == 200,
                  f"decimal limit {value!r} accepted (got {resp.status_code})")
        resp = client.get("/designs/page?limit=2&after_id=1")
        check(resp.status_code == 200, "positive after_id accepted")


def test_legacy_list_endpoint_unchanged():
    with TestClient(app) as client:
        before = {d["id"] for d in client.get("/designs").json()}
        created = [client.post("/designs", json=TWILL).json()["id"] for _ in range(3)]
        resp = client.get("/designs")
        check(resp.status_code == 200, "GET /designs still returns 200")
        listed_ids = {d["id"] for d in resp.json()}
        check(set(created) <= listed_ids and before <= listed_ids,
              "GET /designs still lists every design (old and new)")

        # /designs/page 不抢占 /designs/{id} 路由
        resp = client.get(f"/designs/{created[0]}")
        check(resp.status_code == 200, "GET /designs/{id} route still resolves")


if __name__ == "__main__":
    test_empty_page()
    test_keyset_pagination_walks_all_once()
    test_after_id_is_exclusive()
    test_inserts_during_walk_do_not_duplicate()
    test_invalid_params_return_400()
    test_legacy_list_endpoint_unchanged()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all designs page API tests passed")
