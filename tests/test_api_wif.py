"""GET /designs/{id}/wif 与 POST /designs/import-wif 的接口集成测试。

使用临时 SQLite 文件与 FastAPI TestClient，不依赖运行中的服务，也不触碰
仓库默认的 data/weave.db。直接执行：python3 tests/test_api_wif.py
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

EXPECTED_WIF = """[WIF]
Version=1.1
Name=2/2 twill

[CONTENTS]
TEXTPLAN=yes
TIEUP=yes
THREADING=yes
TREADLING=yes
WEAVING=yes

[TEXTPLAN]
WARP THREADS=8
WEFT THREADS=8

[TIEUP]
1=1,1,0,0
2=0,1,1,0
3=0,0,1,1
4=1,0,0,1

[THREADING]
1=1
2=2
3=3
4=4
5=1
6=2
7=3
8=4

[TREADLING]
1=1
2=2
3=3
4=4
5=1
6=2
7=3
8=4

[WEAVING]
SHAFTS=4
TREADLES=4
CYCLIC=yes
MAX_FLOAT=2
"""


def test_wif_export():
    with TestClient(app) as client:
        # 不存在的设计 -> 404
        resp = client.get("/designs/99999/wif")
        check(resp.status_code == 404, "export missing design returns 404")
        check(resp.json()["detail"] == "design 99999 not found",
              "export 404 detail message")

        design_id = client.post("/designs", json=TWILL).json()["id"]
        resp = client.get(f"/designs/{design_id}/wif")
        check(resp.status_code == 200, "export existing design returns 200")
        ctype = resp.headers["content-type"]
        check(ctype.startswith("text/plain"), f"export content-type is text/plain: {ctype}")
        check(resp.text == EXPECTED_WIF, "export body matches fixed-section 1-based WIF")


def test_wif_roundtrip_preserves_matrix_and_audit():
    with TestClient(app) as client:
        original = client.post("/designs", json=TWILL).json()
        exported = client.get(f"/designs/{original['id']}/wif").text

        # 导出再导入：新设计入库（201）且与原设计交织矩阵、审计结果完全一致
        resp = client.post(
            "/designs/import-wif",
            content=exported,
            headers={"Content-Type": "text/plain"},
        )
        check(resp.status_code == 201, f"import exported WIF returns 201: {resp.text}")
        reimported = resp.json()
        check(reimported["id"] != original["id"], "import creates a new design id")
        for field in ("cyclic", "max_float", "shafts", "treadles",
                      "warp_ends", "weft_picks", "threading", "tie_up",
                      "treadling"):
            check(reimported[field] == original[field],
                  f"round-trip preserves {field}")
        check(reimported["audit"] == original["audit"],
              "round-trip preserves audit (exceeds/interlacing/violations)")
        check(reimported["name"] == original["name"],
              "round-trip preserves design name")

        # 含违规的循环设计再走一圈：违规记录（含跨边界合并段）也必须一致
        risky = {
            "name": "risky cyclic",
            "cyclic": True,
            "max_float": 2,
            "threading": [0],
            "tie_up": [[1], [0]],
            "treadling": [0, 0, 1, 0, 0],
        }
        risky_id = client.post("/designs", json=risky).json()["id"]
        risky_wif = client.get(f"/designs/{risky_id}/wif").text
        risky_in = client.post(
            "/designs/import-wif",
            content=risky_wif,
            headers={"Content-Type": "text/plain"},
        ).json()
        risky_stored = client.get(f"/designs/{risky_id}").json()
        check(risky_in["audit"] == risky_stored["audit"],
              "round-trip preserves cyclic merged float violations")
        check(risky_in["audit"]["exceeds"] is True,
              "risky design import still flags exceeds=true")


def test_wif_import_non_cyclic_defaults_and_explicit_no():
    with TestClient(app) as client:
        plain_wif = """[WIF]
Version=1.1
Name=

[CONTENTS]
TEXTPLAN=yes
TIEUP=yes
THREADING=yes
TREADLING=yes
WEAVING=yes

[TEXTPLAN]
WARP THREADS=4
WEFT THREADS=4

[TIEUP]
1=1,0
2=0,1

[THREADING]
1=1
2=2
3=1
4=2

[TREADLING]
1=1
2=2
3=1
4=2

[WEAVING]
SHAFTS=2
TREADLES=2
CYCLIC=no
MAX_FLOAT=2
"""
        resp = client.post("/designs/import-wif", content=plain_wif)
        check(resp.status_code == 201, f"import plain weave returns 201: {resp.text}")
        data = resp.json()
        check(data["cyclic"] is False, "CYCLIC=no parsed as false")
        check(data["max_float"] == 2, "MAX_FLOAT parsed")
        check(data["name"] is None, "empty Name parses as null")
        check(data["threading"] == [0, 1, 0, 1], "1-based threading converted to 0-based")
        check(data["treadling"] == [0, 1, 0, 1], "1-based treadling converted to 0-based")
        check(data["tie_up"] == [[1, 0], [0, 1]], "tie-up rows parsed in treadle order")
        check(
            data["audit"]["interlacing"]
            == [[1, 0, 1, 0], [0, 1, 0, 1], [1, 0, 1, 0], [0, 1, 0, 1]],
            "imported design produces expected interlacing matrix",
        )

        # 不带 Content-Type（纯文本裸请求体）也应正常解析
        resp2 = client.post("/designs/import-wif", content=plain_wif)
        check(resp2.status_code == 201, "import works without explicit content-type")


def test_wif_import_rejects_malformed_without_writing():
    with TestClient(app) as client:
        good_id = client.post("/designs", json=TWILL).json()["id"]
        ids_before = {d["id"] for d in client.get("/designs").json()}

        def reject(body, msg):
            nonlocal ids_before
            resp = client.post("/designs/import-wif", content=body)
            check(resp.status_code == 400, f"{msg}: 400")
            check("detail" in resp.json(), f"{msg}: error detail present")
            ids_after = {d["id"] for d in client.get("/designs").json()}
            check(ids_after == ids_before, f"{msg}: no design written")
            ids_before = ids_after

        reject("not a wif document at all", "garbage body rejected")
        reject("", "empty body rejected")

        # 版本不对
        reject(EXPECTED_WIF.replace("Version=1.1", "Version=1.0"),
               "wrong WIF version rejected")

        # 缺段落：[WEAVING] 声明了但段落缺失
        reject(EXPECTED_WIF.replace("\n[WEAVING]\n", "\n[OTHER]\n"),
               "missing declared section rejected")

        # [CONTENTS] 声明缺项
        reject(EXPECTED_WIF.replace("WEAVING=yes", "WEAVING=no"),
               "CONTENTS missing WEAVING declaration rejected")

        # 尺寸不一致：SHAFTS 与绑综行宽不符（由 0/1 行宽校验兜出 400）
        reject(EXPECTED_WIF.replace("SHAFTS=4", "SHAFTS=3"),
               "shafts mismatch vs tie-up row width rejected")

        # TEXTPLAN 尺寸与 threading 条目数不符
        reject(EXPECTED_WIF.replace("WARP THREADS=8", "WARP THREADS=7"),
               "warp threads mismatch vs threading entries rejected")

        # 索引越界（1 基综框号超出 SHAFTS）
        reject(EXPECTED_WIF.replace("1=1\n2=2\n3=3\n4=4", "1=5\n2=2\n3=3\n4=4"),
               "threading shaft index out of range rejected")

        # 0 基泄漏：WIF 必须 1 基，0 非法
        reject(EXPECTED_WIF.replace("1=1\n2=2", "1=0\n2=2"),
               "zero-based threading index rejected")

        # 绑综值非 0/1
        reject(EXPECTED_WIF.replace("1=1,1,0,0", "1=1,2,0,0"),
               "tie-up value other than 0/1 rejected")

        # MAX_FLOAT 非法
        reject(EXPECTED_WIF.replace("MAX_FLOAT=2", "MAX_FLOAT=0"),
               "MAX_FLOAT < 1 rejected")

        # CYCLIC 非法取值
        reject(EXPECTED_WIF.replace("CYCLIC=yes", "CYCLIC=true"),
               "CYCLIC other than yes/no rejected")

        # 段落级重复键
        reject(EXPECTED_WIF.replace(
            "TREADLES=4", "TREADLES=4\nSHAFTS=4"),
            "duplicate key in section rejected")

        # 非法 UTF-8
        resp = client.post(
            "/designs/import-wif",
            content=b"[WIF]\xff\xfe garbage",
            headers={"Content-Type": "text/plain"},
        )
        check(resp.status_code == 400, "invalid UTF-8 body rejected")
        ids_after = {d["id"] for d in client.get("/designs").json()}
        check(ids_after == ids_before, "invalid UTF-8 writes nothing")

        # 原设计不受影响
        still = client.get(f"/designs/{good_id}").json()
        check(still["audit"]["interlacing"]
              == client.post("/designs", json=TWILL).json()["audit"]["interlacing"],
              "rejected imports leave existing designs untouched")


def test_wif_export_tolerates_name_without_extra_sections():
    with TestClient(app) as client:
        # 导入端宽容：忽略 [CONTENTS] 中额外声明、本文本子集不使用的段落
        extended = EXPECTED_WIF.replace(
            "[CONTENTS]\n",
            "[CONTENTS]\nWARP=yes\nWEFT=yes\nCOLOR PALETTE=yes\n",
        )
        resp = client.post("/designs/import-wif", content=extended)
        check(resp.status_code == 201, f"extra unknown CONTENTS declarations ignored: {resp.text}")
        data = resp.json()
        check(data["threading"] == TWILL["threading"]
              and data["tie_up"] == TWILL["tie_up"]
              and data["treadling"] == TWILL["treadling"],
              "ignored extra sections leave parsed draft intact")

        # 显式声明了本服务不认识的 WARP=yes，但没有 [WARP] 段落：
        # 不属于子集必需段落，不做检查，照常导入
        check(data["cyclic"] is True and data["max_float"] == 2,
              "extra-declaration import preserves cyclic/max_float")


if __name__ == "__main__":
    test_wif_export()
    test_wif_roundtrip_preserves_matrix_and_audit()
    test_wif_import_non_cyclic_defaults_and_explicit_no()
    test_wif_import_rejects_malformed_without_writing()
    test_wif_export_tolerates_name_without_extra_sections()
    try:
        os.unlink(_TMP_DB.name)
    except OSError:
        pass
    print("all API tests passed")
