"""响应校验接口：维护校验规则，并覆盖响应校验数据集的样本判定、检测窗口与异常队列。

响应校验数据集的处理入口：

- ``POST /api/validator/samples``：提交样本判定（按样本编号 + 检测窗口去重）
- ``GET  /api/validator/samples``：按样本编号检索、查询、定位
- ``GET  /api/validator/samples/{sample_no}``：单样本详情，带回检测窗口与阻断记录
- ``POST /api/validator/refresh``：按数据集重算窗口结论，重复刷新不会再次累计
- ``GET  /api/validator/windows``：检测窗口结论（每个窗口只有一条）
- ``POST /api/validator/windows/{window_no}/close``：关闭检测窗口
- ``GET  /api/validator/queue``：窗口异常待处理队列（汇集阻断结果）
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.validator import RESULT_ORDER, ValidatorService

router = APIRouter(prefix="/api/validator", tags=["响应校验"])

service = ValidatorService()

LIST_FIELDS = ["规则编号", "关联端点", "校验字段", "校验类型", "期望范围", "采样比例", "阻断级别", "告警阈值"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]
SAMPLE_FIELDS = ["样本编号", "检测窗口", "校验字段", "期望范围", "实际值", "判定结果", "判定状态", "到达状态", "到达时间", "备注"]


def _page_size(size: int) -> None:
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按规则编号与状态过滤响应校验列表；没有数据时返回空页，不报错。"""
    _page_size(size)
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/samples", response_model=PageResult[dict])
def list_samples(
    keyword: str | None = Query(default=None, description="按样本编号检索"),
    window: str | None = Query(default=None, description="按检测窗口过滤"),
    result: str | None = Query(default=None, description="通过、告警、阻断、待判定"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """响应校验数据集列表：可按样本编号检索、按检测窗口定位。"""
    _page_size(size)
    if result and result not in RESULT_ORDER:
        raise HTTPException(status_code=400, detail="判定结果只支持通过、告警、阻断、待判定")
    items, total = service.list_samples(
        keyword=keyword, window=window, result=result, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.post("/samples", response_model=ActionResult)
def submit_sample(payload: EntryPayload) -> ActionResult:
    """提交样本判定：同一「样本编号 + 检测窗口」重复提交只保留一次判定，不重复计数。"""
    entry, message, duplicate = service.submit_sample(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    payload_entry = dict(entry)
    payload_entry["重复提交"] = duplicate
    return ActionResult(ok=True, message=message, entry=payload_entry)


@router.post("/refresh", response_model=ActionResult)
def refresh_dataset() -> ActionResult:
    """按响应校验数据集重算检测窗口结论；重复刷新结果一致，不会再次累计。"""
    result = service.refresh()
    return ActionResult(
        ok=True,
        message=result["message"],
        entry={key: value for key, value in result.items() if key not in {"ok", "message"}},
    )


@router.get("/windows", response_model=PageResult[dict])
def list_windows(
    keyword: str | None = Query(default=None, description="按检测窗口或结论说明检索"),
    status: str | None = Query(default=None, description="开启、已关闭"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """检测窗口结论列表：每个检测窗口只有一条可解释结论。"""
    _page_size(size)
    items, total = service.list_windows(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/windows/{window_no}", response_model=dict)
def get_window(window_no: str) -> dict:
    """读取检测窗口的当前结论、窗口内样本和阻断记录。"""
    entry = service.get_window(window_no)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"检测窗口 {window_no} 不存在或尚无样本")
    return entry


@router.post("/windows/{window_no}/close", response_model=ActionResult)
def close_window(window_no: str) -> ActionResult:
    """关闭检测窗口；关闭后的迟到样本只更新该窗口当前结论。"""
    entry, message = service.close_window(window_no)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/queue", response_model=PageResult[dict])
def list_queue(
    keyword: str | None = Query(default=None, description="按检测窗口或阻断样本检索"),
    status: str | None = Query(default=None, description="待处理"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """窗口异常待处理队列：汇集阻断结果，每条记录标注所属检测窗口。"""
    _page_size(size)
    items, total = service.list_queue(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/samples/{sample_no}", response_model=dict)
def get_sample(sample_no: str) -> dict:
    """按样本编号定位单条响应校验记录，带回原检测窗口与同一阻断记录。"""
    entry = service.get_sample(sample_no)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"样本 {sample_no} 不存在或已归档")
    return entry


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出响应校验清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "validator", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条校验规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"校验规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条校验规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="校验规则已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条校验规则执行提交校验、启用规则、停用规则；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
