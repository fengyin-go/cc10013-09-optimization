"""采集监控业务规则：状态流转、字段校验与筛选口径都收在这里。

采集监控读取的是响应校验数据集写下的检测窗口结论：同一个检测窗口只有一条可解释
结论，重复刷新或重复提交都不会让监控看到多条结果。
"""
from __future__ import annotations

from typing import Any

from app.services.validator import ValidatorService
from app.store import store

MODULE = "monitor"
REQUIRED_FIELDS = ["指标编号", "关联端点", "成功率阈值"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用监控": "已启用", "停用监控": "已停用"}
NEGATIVE_ACTIONS = ["停用监控"]

WINDOWS = "validator_windows"


class MonitorService:
    def list_windows(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        conclusion: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        """读取同一检测窗口的当前结论；每个窗口只返回一条，不重复计数。"""
        validator = ValidatorService()
        validator.ensure_windows()
        rows = [dict(row) for row in store.rows(WINDOWS)]
        if keyword:
            needle = keyword.strip().upper()
            rows = [
                row for row in rows
                if needle in str(row.get("检测窗口", "")).upper()
                or needle in str(row.get("结论说明", "")).upper()
            ]
        if status:
            rows = [row for row in rows if row.get("窗口状态") == status.strip()]
        if conclusion:
            rows = [row for row in rows if row.get("结论") == conclusion.strip()]
        total = len(rows)
        start = max(page - 1, 0) * size
        items = []
        for row in rows[start:start + size]:
            items.append({
                "检测窗口": row.get("检测窗口"),
                "窗口状态": row.get("窗口状态"),
                "结论": row.get("结论"),
                "结论说明": row.get("结论说明"),
                "阻断样本数": row.get("阻断样本数", 0),
                "告警样本数": row.get("告警样本数", 0),
                "待判定样本数": row.get("待判定样本数", 0),
                "结论版本": row.get("结论版本"),
                "更新时间": row.get("更新时间"),
            })
        return items, total

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("指标编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"监控指标 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于采集监控可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"监控指标已{action}"
