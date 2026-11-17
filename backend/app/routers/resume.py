"""断点续采恢复接口：维护断点记录，覆盖启动续采、失败释放、重试续采、完成批次、转移归属等动作。

写操作需要 payload 里的「操作团队」与断点所属团队一致；越权请求只能读取归属和处理状态。
明细接口用 team 查询参数做同样的权限判定（中文团队名走 JSON/URL 编码，避免自定义请求头的
Latin-1 乱码问题）。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.resume import ResumeService

router = APIRouter(prefix="/api/resume", tags=["断点续采恢复"])

service = ResumeService()

LIST_FIELDS = ["断点编号", "采集计划", "断点位置", "已采集量", "剩余量", "所属团队", "续采批次", "占用状态", "恢复策略", "恢复耗时", "记录状态"]
STATUSES = ["已记录", "恢复中", "已恢复", "已放弃"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按断点编号检索"),
    status: str | None = Query(default=None, description="已记录、恢复中、已恢复、已放弃"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按断点编号与状态过滤断点续采恢复列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(
    entry_id: int,
    team: str | None = Query(default=None, description="操作团队，用于恢复权限校验"),
) -> dict:
    """读取单条断点记录明细；越权团队只能读取归属和处理状态。"""
    entry = service.get_entry(entry_id, team=team)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"断点记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条断点记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="断点记录已登记，归属待办已同步到分片调度", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条断点记录执行续采动作；越权或仲裁不通过时会被拦下并说明原因。"""
    values = dict(payload.values)
    action = str(values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    readonly = bool(isinstance(entry, dict) and entry.get("只读"))
    return ActionResult(ok=not readonly, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出断点续采恢复清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "resume", "total": total, "items": items}
