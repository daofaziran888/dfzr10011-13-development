"""任务分片调度接口：维护分片任务，覆盖分配节点、重试分片、标记完成等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.shard import ShardService

router = APIRouter(prefix="/api/shard", tags=["任务分片调度"])

service = ShardService()

LIST_FIELDS = ["分片编号", "采集计划", "分片范围", "断点所属团队", "执行团队", "续采批次", "分配节点", "开始时间", "结束时间", "重试次数", "分片状态"]
STATUSES = ["待分配", "执行中", "已完成", "执行失败"]


@router.get("/resume-summary")
def resume_summary() -> dict[str, Any]:
    """断点计划归属汇总清单：由续采占用结果数据驱动，展示断点所属团队与唯一续采批次。"""
    items = service.resume_summary()
    return {"module": "shard", "total": len(items), "items": items}


@router.get("/resume-todos")
def resume_todos() -> dict[str, Any]:
    """断点计划归属待办：其余业务入口更新断点或续采占用结果后这里同步变化。"""
    items = service.resume_todos()
    return {"module": "shard", "total": len(items), "items": items}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按分片编号检索"),
    status: str | None = Query(default=None, description="待分配、执行中、已完成、执行失败"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按分片编号与状态过滤任务分片调度列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分片任务明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分片任务 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分片任务，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分片任务已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分片任务执行分配节点、重试分片、标记完成；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出任务分片调度清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "shard", "total": total, "items": items}
