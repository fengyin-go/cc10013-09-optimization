"""响应校验业务规则：校验规则维护、响应样本判定与检测窗口汇总都收在这里。

响应校验数据集的处理链路：

1. 样本提交（``submit_sample``）：以「样本编号 + 检测窗口」为幂等键写入数据集。
   同一键重复提交只保留一条判定，不会新增样本行，也不会再次累计阻断/告警。
2. 字段判定完成（``判定状态 = 判定完成``）之后才参与窗口汇总；未完成的样本只落库，
   计入「待判定」，不进入阻断/告警统计。
3. 窗口汇总（``_recompute_window``）：汇总结果始终由数据集按检测窗口重算，重复刷新
   幂等；每个检测窗口只维护一条窗口结论。
4. 阻断样本同步到「窗口异常待处理队列」（``_sync_queue``），每个窗口一条队列记录，
   汇集该窗口的全部阻断样本。
5. 窗口关闭后到达的迟到样本（``late``）按「迟到只升不降」更新当前结论，仍只保留
   一条结论记录；刷新不会回退结论，也不会重复计数。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.store import store

MODULE = "validator"
REQUIRED_FIELDS = ["规则编号", "关联端点", "校验字段"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用规则": "已启用", "停用规则": "已停用"}
NEGATIVE_ACTIONS = ["停用规则"]

# 响应校验数据集的内部表：不进运营概览的模块清单，但和业务模块共用同一个 store，
# 监控读取的就是这里写下的检测窗口结论。
SAMPLES = "validator_samples"
WINDOWS = "validator_windows"
QUEUE = "validator_queue"

RESULT_PASS = "通过"
RESULT_WARN = "告警"
RESULT_BLOCK = "阻断"
RESULT_PENDING = "待判定"
RESULT_ORDER = [RESULT_PENDING, RESULT_PASS, RESULT_WARN, RESULT_BLOCK]
RESULT_RANK = {name: index for index, name in enumerate(RESULT_ORDER)}

JUDGED = "判定完成"
UNJUDGED = "待判定"
NORMAL_ARRIVAL = "正常"
LATE_ARRIVAL = "迟到"
QUEUE_PENDING = "待处理"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def _sample_key(sample_no: str, window_no: str) -> tuple[str, str]:
    return sample_no.strip().upper(), window_no.strip().upper()


class ValidatorService:
    # ------------------------------------------------------------------ 校验规则
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
            rows = [row for row in rows if keyword in str(row.get("规则编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not _text(values.get(field))]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": _next_id(rows)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"校验规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于响应校验可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"校验规则已{action}"

    # ------------------------------------------------------------ 响应校验数据集
    def ensure_windows(self) -> None:
        """补齐没有窗口结论的样本窗口；已有结论的窗口不重算，避免读取动作产生副作用。"""
        known = {_text(row.get("检测窗口")) for row in store.rows(WINDOWS)}
        windows: list[str] = []
        for sample in store.rows(SAMPLES):
            window_no = _text(sample.get("检测窗口"))
            if window_no and window_no not in known and window_no not in windows:
                windows.append(window_no)
        for window_no in windows:
            self._recompute_window(window_no)

    def list_samples(
        self,
        *,
        keyword: str | None = None,
        window: str | None = None,
        result: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        self.ensure_windows()
        samples = store.rows(SAMPLES)
        if keyword:
            needle = keyword.strip().upper()
            samples = [
                row for row in samples
                if needle in _text(row.get("样本编号")).upper()
            ]
        if window:
            samples = [row for row in samples if _text(row.get("检测窗口")) == window.strip()]
        if result:
            samples = [row for row in samples if _text(row.get("判定结果")) == result.strip()]
        total = len(samples)
        start = max(page - 1, 0) * size
        return [self._decorate_sample(row) for row in samples[start:start + size]], total

    def get_sample(self, sample_no: str) -> dict[str, Any] | None:
        """按样本编号定位数据集记录，并带回它所属的检测窗口与阻断记录。"""
        self.ensure_windows()
        needle = _text(sample_no).upper()
        for row in store.rows(SAMPLES):
            if _text(row.get("样本编号")).upper() == needle:
                return self._decorate_sample(row, nested=True)
        return None

    def submit_sample(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str, bool]:
        """提交一条样本判定；以样本编号 + 检测窗口去重。

        返回值第三项表示是否为重复提交（重复提交不新增判定、不重复计数）。
        """
        self.ensure_windows()
        sample_no = _text(values.get("样本编号"))
        window_no = _text(values.get("检测窗口"))
        if not sample_no or not window_no:
            return None, "缺少必填字段：样本编号、检测窗口", False

        raw_result = _text(values.get("判定结果"))
        if raw_result and raw_result not in (RESULT_PASS, RESULT_WARN, RESULT_BLOCK):
            return None, f"判定结果「{raw_result}」不在允许范围（通过、告警、阻断）内", False
        result = raw_result or RESULT_PENDING
        judged = result != RESULT_PENDING
        judgment_status = JUDGED if judged else UNJUDGED
        arrival = LATE_ARRIVAL if _text(values.get("到达状态")) == LATE_ARRIVAL else NORMAL_ARRIVAL
        now = _now()

        submitted = {
            "校验字段": _text(values.get("校验字段")),
            "期望范围": _text(values.get("期望范围")),
            "实际值": _text(values.get("实际值")),
            "判定结果": result,
            "判定状态": judgment_status,
            "到达状态": arrival,
            "到达时间": _text(values.get("到达时间")) or now,
            "备注": _text(values.get("备注")),
        }

        existing = self._find_sample(sample_no, window_no)
        if existing is not None:
            window = self._find_window(window_no)
            closed_now = window is not None and window.get("窗口状态") == "已关闭"
            if closed_now:
                # 窗口已关闭：这次提交按迟到样本处理，终态只升不降。
                submitted["到达状态"] = LATE_ARRIVAL
            unchanged = all(
                _text(existing.get(field)) == value
                for field, value in submitted.items()
            )
            if unchanged:
                return (
                    self._decorate_sample(existing, nested=True),
                    f"重复提交已忽略：样本 {sample_no} 在检测窗口 {window_no} 已有判定，"
                    "未新增判定，也未再次累计阻断与告警",
                    True,
                )
            # 同一「样本 + 检测窗口」只保留一条判定：用新判定覆盖原记录，
            # 窗口结论与异常队列都由数据集整体重算，不会叠加计数。
            existing.update(submitted)
            existing["阻断标记"] = result == RESULT_BLOCK
            existing["告警标记"] = result == RESULT_WARN
            existing["最近提交时间"] = now
            existing["提交次数"] = int(existing.get("提交次数", 1)) + 1
            existing["判定修订次数"] = int(existing.get("判定修订次数", 0)) + 1
            late = arrival == LATE_ARRIVAL or closed_now
            # 只有窗口关闭后提交/新并入的迟到样本才记入迟到列表；重复提交不重复记。
            self._recompute_window(
                window_no, late_sample=existing["样本编号"] if late else None
            )
            return (
                self._decorate_sample(existing, nested=True),
                f"样本 {sample_no} 在检测窗口 {window_no} 的判定已更新；"
                "同一检测窗口只保留一条判定，阻断与告警按最新判定重算",
                False,
            )

        entry = {
            "id": _next_id(store.rows(SAMPLES)),
            "样本编号": sample_no,
            "检测窗口": window_no,
            "首次提交时间": now,
            "最近提交时间": now,
            "提交次数": 1,
            "判定修订次数": 0,
            **submitted,
            "阻断标记": result == RESULT_BLOCK,
            "告警标记": result == RESULT_WARN,
        }
        store.rows(SAMPLES).append(entry)

        window = self._find_window(window_no)
        late = arrival == LATE_ARRIVAL or (
            window is not None and window.get("窗口状态") == "已关闭"
        )
        if late:
            entry["到达状态"] = LATE_ARRIVAL
        self._recompute_window(window_no, late_sample=sample_no if late else None)
        action = "迟到样本已并入"
        message = (
            f"{action}检测窗口 {window_no}，只更新该窗口的当前结论，未新增窗口结论"
            if late else f"样本 {sample_no} 已写入检测窗口 {window_no}"
        )
        if judged:
            message += "；字段判定完成，已参与阻断与告警汇总"
        else:
            message += "；字段判定未完成，暂不参与阻断与告警汇总"
        return self._decorate_sample(entry, nested=True), message, False

    def list_windows(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        self.ensure_windows()
        rows = store.rows(WINDOWS)
        if keyword:
            needle = keyword.strip().upper()
            rows = [
                row for row in rows
                if needle in _text(row.get("检测窗口")).upper()
                or needle in _text(row.get("结论说明")).upper()
            ]
        if status:
            rows = [row for row in rows if _text(row.get("窗口状态")) == status.strip()]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._decorate_window(row) for row in rows[start:start + size]], total

    def get_window(self, window_no: str) -> dict[str, Any] | None:
        self.ensure_windows()
        window = self._find_window(window_no)
        if window is None:
            return None
        payload = self._decorate_window(window)
        payload["样本列表"] = [
            self._decorate_sample(row)
            for row in store.rows(SAMPLES)
            if _text(row.get("检测窗口")) == _text(window.get("检测窗口"))
        ]
        payload["阻断记录"] = self._find_queue(window_no)
        return payload

    def close_window(self, window_no: str) -> tuple[dict[str, Any] | None, str]:
        self.ensure_windows()
        window = self._find_window(window_no)
        if window is None:
            return None, f"检测窗口 {window_no} 不存在，无法关闭"
        if window.get("窗口状态") == "已关闭":
            return self._decorate_window(window), f"检测窗口 {window_no} 已关闭，结论保持不变"
        window["窗口状态"] = "已关闭"
        window["关闭时间"] = _now()
        store.closed_windows[_text(window_no).upper()] = window["关闭时间"]
        self._recompute_window(window_no)
        window = self._find_window(window_no)
        return self._decorate_window(window), (
            f"检测窗口 {window_no} 已关闭；关闭后到达的迟到样本只更新当前结论，不新增结论"
        )

    def list_queue(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        self.ensure_windows()
        rows = store.rows(QUEUE)
        if keyword:
            needle = keyword.strip().upper()
            rows = [
                row for row in rows
                if needle in _text(row.get("检测窗口")).upper()
                or needle in _text(row.get("阻断样本")).upper()
            ]
        if status:
            rows = [row for row in rows if _text(row.get("状态")) == status.strip()]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [dict(row) for row in rows[start:start + size]], total

    def refresh(self) -> dict[str, Any]:
        """按数据集重算全部窗口与异常队列；重复刷新结果一致，不会重复累计。"""
        self.ensure_windows()
        window_numbers = [_text(row.get("检测窗口")) for row in store.rows(WINDOWS)]
        for window_no in window_numbers:
            self._recompute_window(window_no)
        return {
            "ok": True,
            "message": "检测窗口结论已按响应校验数据集刷新，重复刷新不会再次累计",
            "samples": len(store.rows(SAMPLES)),
            "windows": len(store.rows(WINDOWS)),
            "queue": len(store.rows(QUEUE)),
        }

    # --------------------------------------------------------------- 内部计算
    def _find_sample(self, sample_no: str, window_no: str) -> dict[str, Any] | None:
        key = _sample_key(sample_no, window_no)
        for row in store.rows(SAMPLES):
            if _sample_key(_text(row.get("样本编号")), _text(row.get("检测窗口"))) == key:
                return row
        return None

    def _find_window(self, window_no: str) -> dict[str, Any] | None:
        needle = _text(window_no).upper()
        for row in store.rows(WINDOWS):
            if _text(row.get("检测窗口")).upper() == needle:
                return row
        return None

    def _find_queue(self, window_no: str) -> dict[str, Any] | None:
        needle = _text(window_no).upper()
        for row in store.rows(QUEUE):
            if _text(row.get("检测窗口")).upper() == needle:
                return row
        return None

    def _window_samples(self, window_no: str) -> list[dict[str, Any]]:
        needle = _text(window_no).upper()
        return [
            row for row in store.rows(SAMPLES)
            if _text(row.get("检测窗口")).upper() == needle
        ]

    def _aggregate(self, window_no: str) -> dict[str, Any]:
        samples = self._window_samples(window_no)
        completed = [row for row in samples if row.get("判定状态") == JUDGED]
        blocks = [row for row in completed if row.get("判定结果") == RESULT_BLOCK]
        warnings = [row for row in completed if row.get("判定结果") == RESULT_WARN]
        passes = [row for row in completed if row.get("判定结果") == RESULT_PASS]
        pending = [row for row in samples if row.get("判定状态") != JUDGED]

        if blocks:
            level = RESULT_BLOCK
        elif warnings:
            level = RESULT_WARN
        elif passes:
            level = RESULT_PASS
        else:
            level = RESULT_PENDING

        return {
            "样本数": len(samples),
            "已判定样本数": len(completed),
            "阻断样本数": len(blocks),
            "告警样本数": len(warnings),
            "通过样本数": len(passes),
            "待判定样本数": len(pending),
            "结论": level,
            "阻断样本": sorted(_text(row.get("样本编号")) for row in blocks),
            "告警样本": sorted(_text(row.get("样本编号")) for row in warnings),
        }

    def _explain(
        self,
        window_no: str,
        metrics: dict[str, Any],
        *,
        closed: bool,
        late_samples: list[str],
        locked_from: str | None = None,
    ) -> str:
        total = metrics["样本数"]
        done = metrics["已判定样本数"]
        level = metrics["结论"]
        if level == RESULT_BLOCK:
            line = (
                f"检测窗口 {window_no} 共 {total} 个样本，{done} 个已完成字段判定，"
                f"其中 {metrics['阻断样本数']} 个样本判定为阻断"
                f"（{'、'.join(metrics['阻断样本'])}），汇总为阻断结论"
            )
        elif level == RESULT_WARN:
            line = (
                f"检测窗口 {window_no} 共 {total} 个样本，{done} 个已完成字段判定，"
                f"其中 {metrics['告警样本数']} 个样本判定为告警"
                f"（{'、'.join(metrics['告警样本'])}），未出现阻断样本，汇总为告警结论"
            )
        elif level == RESULT_PASS:
            line = (
                f"检测窗口 {window_no} 共 {total} 个样本，{done} 个已完成字段判定，"
                "全部通过字段判定，汇总为通过结论"
            )
        else:
            line = (
                f"检测窗口 {window_no} 共 {total} 个样本，字段判定尚未完成，"
                "暂不汇总阻断与告警"
            )
        if metrics["待判定样本数"]:
            line += f"；{metrics['待判定样本数']} 个样本未完成字段判定，未计入汇总"
        if metrics["阻断样本数"]:
            line += "；阻断样本已写入窗口异常待处理队列"
        if closed:
            line += "；窗口已关闭，结论按迟到只升不降锁定"
            if locked_from:
                line += f"，窗口内迟到样本未改变已锁定的{locked_from}结论"
        if late_samples:
            line += f"；窗口关闭后收到迟到样本 {'、'.join(sorted(late_samples))}，只更新该窗口当前结论"
        return line + "。"

    def _recompute_window(
        self,
        window_no: str,
        *,
        late_sample: str | None = None,
    ) -> dict[str, Any] | None:
        """重算单个检测窗口：只保留一条窗口结论，重复调用结果幂等。"""
        samples = self._window_samples(window_no)
        if not samples:
            return None

        metrics = self._aggregate(window_no)
        window = self._find_window(window_no)
        now = _now()
        closed_at = store.closed_windows.get(_text(window_no).upper())
        if window is None:
            window = {
                "id": _next_id(store.rows(WINDOWS)),
                "检测窗口": _text(window_no),
                "窗口状态": "已关闭" if closed_at else "开启",
                "结论": metrics["结论"],
                "结论说明": "",
                "结论版本": 1,
                "迟到样本列表": [],
                "创建时间": now,
                "更新时间": now,
                "关闭时间": closed_at,
            }
            store.rows(WINDOWS).append(window)

        closed = window.get("窗口状态") == "已关闭"
        previous_level = _text(window.get("结论")) or RESULT_PENDING
        level = metrics["结论"]
        if closed:
            # 已关闭窗口的终态只升不降：迟到通过样本不会把阻断/告警结论降级。
            if RESULT_RANK[level] < RESULT_RANK[previous_level]:
                level = previous_level

        late_samples = list(window.get("迟到样本列表") or [])
        if late_sample:
            name = _text(late_sample)
            if name and name not in late_samples:
                late_samples.append(name)

        version = int(window.get("结论版本", 1))
        if late_sample or level != previous_level:
            # 结论变化，或迟到样本并入已关闭窗口：当前结论的解释需要刷新版本。
            version += 1

        updated = {
            "样本数": metrics["样本数"],
            "已判定样本数": metrics["已判定样本数"],
            "阻断样本数": metrics["阻断样本数"],
            "告警样本数": metrics["告警样本数"],
            "通过样本数": metrics["通过样本数"],
            "待判定样本数": metrics["待判定样本数"],
            "结论": level,
            "结论说明": self._explain(
                _text(window.get("检测窗口")),
                {**metrics, "结论": level},
                closed=closed,
                late_samples=late_samples,
                locked_from=previous_level if level != metrics["结论"] else None,
            ),
            "结论版本": version,
            "迟到样本列表": late_samples,
        }
        if any(window.get(key) != value for key, value in updated.items()):
            window.update(updated)
            window["更新时间"] = now

        self._sync_queue(window)
        return window

    def _sync_queue(self, window: dict[str, Any]) -> dict[str, Any] | None:
        """每个窗口只维护一条异常待处理记录，汇集该窗口全部阻断样本。"""
        window_no = _text(window.get("检测窗口"))
        blocks = sorted(
            _text(row.get("样本编号"))
            for row in self._window_samples(window_no)
            if row.get("判定状态") == JUDGED and row.get("判定结果") == RESULT_BLOCK
        )
        existing = self._find_queue(window_no)
        if not blocks:
            if existing is not None:
                store.rows(QUEUE).remove(existing)
            return None

        payload = {
            "检测窗口": window_no,
            "阻断样本": "、".join(blocks),
            "阻断样本列表": blocks,
            "阻断数量": len(blocks),
            "阻断原因": f"{len(blocks)} 个样本字段判定未通过：{'、'.join(blocks)}",
            "状态": QUEUE_PENDING,
            "结论版本": int(window.get("结论版本", 1)),
        }
        now = _now()
        if existing is not None:
            if any(existing.get(key) != value for key, value in payload.items()):
                existing.update(payload)
                existing["更新时间"] = now
            return existing
        entry = {
            "id": _next_id(store.rows(QUEUE)),
            "队列编号": f"QUEU-{_next_id(store.rows(QUEUE)):04d}",
            **payload,
            "创建时间": now,
            "更新时间": now,
        }
        store.rows(QUEUE).append(entry)
        return entry

    def _decorate_sample(
        self,
        sample: dict[str, Any],
        *,
        nested: bool = False,
    ) -> dict[str, Any]:
        window = self._find_window(_text(sample.get("检测窗口")))
        queue = self._find_queue(_text(sample.get("检测窗口")))
        payload = dict(sample)
        payload["窗口状态"] = window.get("窗口状态") if window else None
        payload["窗口结论"] = window.get("结论") if window else None
        payload["结论说明"] = window.get("结论说明") if window else None
        payload["阻断记录编号"] = queue.get("队列编号") if queue else None
        if nested:
            payload["检测窗口记录"] = dict(window) if window else None
            payload["阻断记录"] = dict(queue) if queue else None
        return payload

    def _decorate_window(self, window: dict[str, Any]) -> dict[str, Any]:
        payload = dict(window)
        queue = self._find_queue(_text(window.get("检测窗口")))
        payload["队列编号"] = queue.get("队列编号") if queue else None
        payload["队列状态"] = queue.get("状态") if queue else None
        payload["阻断样本明细"] = queue.get("阻断样本列表") if queue else []
        return payload
