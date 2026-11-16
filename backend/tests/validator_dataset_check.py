"""响应校验数据集的复测脚本：直接调用服务层，覆盖去重、刷新幂等与迟到样本。

用法：cd backend && .venv/bin/python tests/validator_dataset_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.validator import ValidatorService  # noqa: E402
from app.store import store  # noqa: E402

SERVICE = ValidatorService()

SAMP_91 = {
    "样本编号": "SAMP-91",
    "检测窗口": "WIN-17",
    "校验字段": "data.records[*].status",
    "期望范围": "取值在 allowed_status 内",
    "实际值": "unknown_status",
    "判定结果": "阻断",
    "到达状态": "正常",
    "到达时间": "2026-11-16 09:12:03",
    "备注": "状态枚举越界",
}

TRACE_93 = {
    "样本编号": "TRACE-93",
    "检测窗口": "WIN-18",
    "校验字段": "data.meta.trace_id",
    "期望范围": "非空且 32 位十六进制",
    "实际值": "",
    "判定结果": "阻断",
    "到达状态": "迟到",
    "到达时间": "2026-11-16 10:20:00",
    "备注": "trace_id 缺失",
}


def counts() -> tuple[int, int, int]:
    return (
        len(store.rows("validator_samples")),
        len(store.rows("validator_windows")),
        len(store.rows("validator_queue")),
    )


def window(window_no: str) -> dict:
    found = SERVICE.get_window(window_no)
    assert found is not None, window_no
    return found


def main() -> int:
    SERVICE.ensure_windows()
    # WIN-17 有阻断样本 -> 一条队列记录；WIN-18 关闭前只有通过样本 -> 暂无队列记录。
    base = counts()
    assert base == (4, 2, 1), base

    # 1. 同一「样本 + 检测窗口」重复提交：只保留一条判定，不重复累计阻断。
    for _ in range(3):
        entry, message, duplicate = SERVICE.submit_sample(SAMP_91)
        assert entry is not None and duplicate, message
        assert counts() == base, counts()
    assert window("WIN-17")["阻断样本数"] == 2
    assert window("WIN-17")["阻断样本明细"] == ["SAMP-91", "SAMP-92"]
    assert window("WIN-17")["结论"] == "阻断"

    # 2. 刷新是幂等的：重复刷新结论与队列数量不变。
    for _ in range(3):
        SERVICE.refresh()
    assert counts() == base, counts()
    assert window("WIN-17")["结论版本"] == 1

    # 3. 已关闭窗口收到迟到样本：只更新当前结论，不新增窗口/队列。
    entry, message, duplicate = SERVICE.submit_sample(TRACE_93)
    assert entry is not None and not duplicate, message
    assert counts() == (5, 2, 2), counts()
    win18 = window("WIN-18")
    assert win18["窗口状态"] == "已关闭"
    assert win18["结论"] == "阻断"
    assert win18["迟到样本列表"] == ["TRACE-93"]
    assert win18["结论版本"] == 2, win18["结论版本"]

    # 4. 迟到样本重复提交：仍然只有一条窗口结论，结论版本不再增长。
    _, _, duplicate = SERVICE.submit_sample(TRACE_93)
    assert duplicate
    assert counts() == (5, 2, 2)
    assert window("WIN-18")["结论版本"] == 2

    # 5. 关闭后到达的迟到通过样本不能把阻断终态降级。
    SERVICE.submit_sample({**TRACE_93, "样本编号": "SAMP-94", "判定结果": "通过", "实际值": "ok"})
    win18 = window("WIN-18")
    assert win18["结论"] == "阻断"
    assert win18["阻断样本数"] == 1, win18["阻断样本数"]
    assert counts() == (6, 2, 2), counts()

    # 6. 按样本编号定位：返回原检测窗口与同一阻断记录。
    for sample_no in ("SAMP-91", "SAMP-92"):
        sample = SERVICE.get_sample(sample_no)
        assert sample is not None
        assert sample["检测窗口"] == "WIN-17"
        assert sample["阻断记录编号"] == "QUEU-0001"
        assert sample["阻断记录"]["检测窗口"] == "WIN-17"
    trace = SERVICE.get_sample("TRACE-93")
    assert trace is not None and trace["检测窗口"] == "WIN-18"
    assert trace["阻断记录"]["检测窗口"] == "WIN-18"

    # 7. 搜索与窗口定位。
    items, total = SERVICE.list_samples(keyword="TRACE")
    assert total == 1 and items[0]["样本编号"] == "TRACE-93"
    items, total = SERVICE.list_samples(window="WIN-17")
    assert total == 3 and {row["样本编号"] for row in items} == {"SAMP-90", "SAMP-91", "SAMP-92"}

    # 8. 待判定样本不参与阻断/告警汇总。
    before = window("WIN-17")["结论"]
    SERVICE.submit_sample({"样本编号": "SAMP-95", "检测窗口": "WIN-17"})
    pending = window("WIN-17")
    assert pending["结论"] == before
    assert pending["待判定样本数"] == 1
    assert pending["阻断样本数"] == 2

    # 9. 字段判定完成后才参与汇总。
    SERVICE.submit_sample({"样本编号": "SAMP-95", "检测窗口": "WIN-17", "判定结果": "阻断"})
    judged = window("WIN-17")
    assert judged["阻断样本数"] == 3
    assert judged["待判定样本数"] == 0

    print("响应校验数据集复测通过：去重、刷新幂等、迟到样本与终态锁定均符合预期。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
