"""续采占用仲裁：校验恢复权限、仲裁断点位置占用、维护续采占用结果数据。

所有业务入口（断点登记、启动续采、失败释放、重试续采、归属转移……）都经由这里
写入续采占用结果，并同步驱动分片模块的断点计划归属汇总清单与归属待办：

- 同一断点位置同一时刻只授予一个续采批次；并发/重复启动返回既有占用结果。
- 失败只释放本次未使用范围，断点水位（断点位置、已采集量）只前进不回退。
- 失败重试复用原批次，不会占用第二份恢复位置。
- 归属转移后旧批次仍保留授予时的原团队依据。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store

TABLE = "resume_occupation"
RESUME_TABLE = "resume"
SHARD_TABLE = "shard"

# 占用记录状态
BATCH_ACTIVE = "占用中"
BATCH_FAILED = "已失败"
BATCH_DONE = "已完成"

# 归属交接方式
MODE_FINISH_BY_SOURCE = "原团队完成当前批次"
MODE_TAKEOVER_BY_TARGET = "新团队接管当前批次"
TRANSFER_MODES = [MODE_FINISH_BY_SOURCE, MODE_TAKEOVER_BY_TARGET]

# 检查并授予必须原子完成，否则并发启动会在同一断点位置授予两个批次。
_grant_lock = threading.Lock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_int(value: Any) -> int:
    """断点位置、已采集量等水位字段按整数处理；历史样例数据不是数字时按 0 算。"""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _occupations() -> list[dict[str, Any]]:
    return store.rows(TABLE)


def _next_batch_no() -> str:
    seq = max((int(row.get("id", 0)) for row in _occupations()), default=0) + 1
    return f"RB-{seq:04d}"


def _next_shard_no() -> str:
    seq = max((int(row.get("id", 0)) for row in store.rows(SHARD_TABLE)), default=0) + 1
    return f"SHAR-{seq:04d}"


def _active_batch(breakpoint_no: str) -> dict[str, Any] | None:
    """断点当前仍有效的占用记录（占用中或已失败待重试都算占用着恢复位置）。

    仲裁以断点编号为准：断点水位随采集推进而变化，但一个断点的恢复位置
    同一时刻只能授予一个续采批次。
    """
    for row in _occupations():
        if row.get("断点编号") == breakpoint_no and row.get("占用状态") in (BATCH_ACTIVE, BATCH_FAILED):
            return row
    return None


def _append_history(entry: dict[str, Any], event: str, detail: str) -> None:
    history = entry.setdefault("断点历史", [])
    history.append({"时间": _now(), "事件": event, "说明": detail})


def _bind_shard_range(entry: dict[str, Any], batch: dict[str, Any]) -> None:
    """分片调度读取续采占用结果：把恢复批次的分片范围绑定到分片任务上。"""
    rows = store.rows(SHARD_TABLE)
    bound = next((row for row in rows if row.get("续采批次") == batch.get("批次号")), None)
    if bound is None:
        bound = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "分片编号": _next_shard_no(),
            "分配节点": "待分配",
            "开始时间": "",
            "结束时间": "",
            "重试次数": "0",
            "分片状态": "续采恢复",
            "pending": True,
            "abnormal": False,
        }
        rows.append(bound)
    bound["采集计划"] = entry.get("采集计划", "")
    bound["分片范围"] = batch.get("占用范围", "")
    bound["断点所属团队"] = entry.get("所属团队", "")
    bound["执行团队"] = batch.get("执行团队", "")
    bound["续采批次"] = batch.get("批次号", "")
    bound["status"] = "执行中"


def _sync_shard_status(batch: dict[str, Any], status: str, shard_status: str) -> None:
    for row in store.rows(SHARD_TABLE):
        if row.get("续采批次") == batch.get("批次号"):
            row["status"] = status
            row["分片状态"] = shard_status
            row["pending"] = status not in ("已完成",)
            row["abnormal"] = status == "执行失败"


class OccupationService:
    """续采批次的授予、释放、重试与归属交接都收在这一个服务里。"""

    # ------------------------------------------------------------------ 权限
    @staticmethod
    def can_operate(entry: dict[str, Any], team: str) -> bool:
        """恢复权限校验：断点所属团队，或归属交接后仍在执行当前批次的原团队。"""
        if not team:
            return False
        if team == str(entry.get("所属团队", "")):
            return True
        batch_no = str(entry.get("续采批次", "") or "")
        if not batch_no:
            return False
        for row in _occupations():
            if row.get("批次号") == batch_no and row.get("占用状态") == BATCH_ACTIVE:
                return team == str(row.get("执行团队", ""))
        return False

    @staticmethod
    def readonly_view(entry: dict[str, Any]) -> dict[str, Any]:
        """越权请求的只读视图：只能看到归属和处理状态。"""
        return {
            "断点编号": entry.get("断点编号", ""),
            "采集计划": entry.get("采集计划", ""),
            "所属团队": entry.get("所属团队", ""),
            "处理状态": entry.get("status", ""),
            "只读": True,
            "提示": "越权请求仅可读取归属和处理状态，不能改动断点与续采批次",
        }

    # ------------------------------------------------------------------ 授予
    def grant(self, entry: dict[str, Any], team: str) -> tuple[dict[str, Any] | None, str, bool]:
        """启动续采：校验恢复权限并仲裁断点位置占用，返回续采占用结果。

        返回 (占用结果, 说明, 是否本次新授予)。越权或仲裁失败时占用结果为 None。
        """
        if not self.can_operate(entry, team):
            return None, (
                f"越权操作：当前团队「{team or '未登记'}」不是断点所属团队"
                f"「{entry.get('所属团队', '')}」，仅可读取归属和处理状态"
            ), False
        position = str(entry.get("断点位置", ""))
        with _grant_lock:
            existing = _active_batch(str(entry.get("断点编号", "")))
            if existing is not None:
                if existing.get("占用状态") == BATCH_ACTIVE:
                    # 重复启动（含并发竞争失败方）：返回既有占用结果，不重复授予。
                    return existing, f"重复启动：断点位置 {position} 已授予批次 {existing.get('批次号')}，返回既有占用结果", False
                # 已失败的批次：重试复用原批次，不能占用第二份恢复位置。
                self._reactivate(entry, existing)
                return existing, f"失败重试：复用既有批次 {existing.get('批次号')}，未占用第二份恢复位置", False

            remaining = _to_int(entry.get("剩余量"))
            if remaining <= 0:
                return None, f"断点位置 {position} 的剩余量为 0，无可续采范围", False
            batch = {
                "id": max((int(row.get("id", 0)) for row in _occupations()), default=0) + 1,
                "批次号": _next_batch_no(),
                "断点编号": entry.get("断点编号", ""),
                "采集计划": entry.get("采集计划", ""),
                "断点位置": position,
                "授予团队": entry.get("所属团队", ""),  # 原团队依据，永久保留
                "执行团队": entry.get("所属团队", ""),
                "占用范围": f"{_to_int(position)}-{_to_int(position) + remaining}",
                "已用量": 0,
                "未用量": remaining,
                "已释放范围": "",
                "占用状态": BATCH_ACTIVE,
                "交接说明": "",
                "授予时间": _now(),
                "更新时间": _now(),
            }
            _occupations().append(batch)
            entry["续采批次"] = batch["批次号"]
            entry["占用状态"] = "占用中"
            entry["status"] = "恢复中"
            entry["pending"] = True
            _append_history(entry, "授予续采批次", f"批次 {batch['批次号']} 授予团队 {batch['授予团队']}，占用范围 {batch['占用范围']}")
            _bind_shard_range(entry, batch)
            return batch, f"续采批次 {batch['批次号']} 已授予，占用范围 {batch['占用范围']}", True

    def _reactivate(self, entry: dict[str, Any], batch: dict[str, Any]) -> None:
        """失败批次重试：同一批次在原断点位置重新占用剩余范围。"""
        remaining = _to_int(entry.get("剩余量"))
        position = _to_int(entry.get("断点位置"))
        batch["占用状态"] = BATCH_ACTIVE
        batch["占用范围"] = f"{position}-{position + remaining}"
        batch["未用量"] = remaining
        batch["更新时间"] = _now()
        entry["续采批次"] = batch["批次号"]
        entry["占用状态"] = "占用中"
        entry["status"] = "恢复中"
        entry["pending"] = True
        _append_history(entry, "重试续采批次", f"批次 {batch['批次号']} 在原恢复位置重试，占用范围 {batch['占用范围']}")
        _bind_shard_range(entry, batch)

    # ------------------------------------------------------------------ 释放
    def release_unused(self, entry: dict[str, Any], team: str, used: int) -> tuple[dict[str, Any] | None, str]:
        """失败释放：只释放本次未使用范围，断点水位按已用量前进、绝不回退。"""
        if not self.can_operate(entry, team):
            return None, (
                f"越权操作：当前团队「{team or '未登记'}」不是断点所属团队"
                f"「{entry.get('所属团队', '')}」，仅可读取归属和处理状态"
            )
        batch = self._current_batch(entry)
        if batch is None or batch.get("占用状态") != BATCH_ACTIVE:
            return None, "当前没有占用中的续采批次，无法执行失败释放"
        used = max(0, min(used, _to_int(batch.get("未用量"))))
        released = _to_int(batch.get("未用量")) - used
        position = _to_int(entry.get("断点位置"))
        # 水位只前进：已采集量与断点位置按本次已用量推进，失败不允许回退。
        entry["已采集量"] = str(_to_int(entry.get("已采集量")) + used)
        entry["断点位置"] = str(position + used)
        entry["剩余量"] = str(_to_int(entry.get("剩余量")) - used)
        batch["已用量"] = _to_int(batch.get("已用量")) + used
        batch["未用量"] = 0
        batch["已释放范围"] = f"{position + used}-{position + used + released}" if released > 0 else ""
        batch["占用状态"] = BATCH_FAILED
        batch["更新时间"] = _now()
        entry["占用状态"] = "空闲"
        entry["status"] = "已记录"
        _append_history(
            entry,
            "失败释放",
            f"批次 {batch['批次号']} 本次已用 {used}，释放未使用范围 {batch['已释放范围'] or '无'}，断点水位保持 {entry['断点位置']} 不回退",
        )
        _sync_shard_status(batch, "执行失败", "续采失败")
        return batch, f"批次 {batch['批次号']} 已标记失败，仅释放本次未使用范围，断点水位未回退"

    # ------------------------------------------------------------------ 完成
    def complete(self, entry: dict[str, Any], team: str) -> tuple[dict[str, Any] | None, str]:
        """完成当前批次：剩余范围全部并入水位，占用解除。"""
        if not self.can_operate(entry, team):
            return None, (
                f"越权操作：当前团队「{team or '未登记'}」不是断点所属团队"
                f"「{entry.get('所属团队', '')}」，仅可读取归属和处理状态"
            )
        batch = self._current_batch(entry)
        if batch is None or batch.get("占用状态") != BATCH_ACTIVE:
            return None, "当前没有占用中的续采批次，无法完成批次"
        remaining = _to_int(entry.get("剩余量"))
        position = _to_int(entry.get("断点位置"))
        entry["已采集量"] = str(_to_int(entry.get("已采集量")) + remaining)
        entry["断点位置"] = str(position + remaining)
        entry["剩余量"] = "0"
        batch["已用量"] = _to_int(batch.get("已用量")) + remaining
        batch["未用量"] = 0
        batch["占用状态"] = BATCH_DONE
        batch["更新时间"] = _now()
        entry["占用状态"] = "空闲"
        entry["status"] = "已恢复"
        entry["pending"] = False
        _append_history(entry, "批次完成", f"批次 {batch['批次号']} 由 {batch['执行团队']} 执行完成，断点水位推进到 {entry['断点位置']}")
        _sync_shard_status(batch, "已完成", "续采完成")
        return batch, f"批次 {batch['批次号']} 已完成，断点水位推进到 {entry['断点位置']}"

    # ------------------------------------------------------------------ 交接
    def transfer(
        self,
        entry: dict[str, Any],
        *,
        new_team: str,
        mode: str,
        operator_team: str,
    ) -> tuple[dict[str, Any] | None, str]:
        """采集计划归属转移：续采批次执行中转团队的两种冲突处理方式。"""
        if not self.can_operate(entry, operator_team):
            return None, (
                f"越权操作：当前团队「{operator_team or '未登记'}」不是断点所属团队"
                f"「{entry.get('所属团队', '')}」，仅可读取归属和处理状态"
            )
        old_team = str(entry.get("所属团队", ""))
        if not new_team or new_team == old_team:
            return None, "新归属团队不能为空，且不能与原团队相同"
        if mode not in TRANSFER_MODES:
            return None, f"交接方式「{mode}」不支持，可选：{'、'.join(TRANSFER_MODES)}"

        batch = self._current_batch(entry)
        active = batch is not None and batch.get("占用状态") == BATCH_ACTIVE
        entry["所属团队"] = new_team
        if active and mode == MODE_FINISH_BY_SOURCE:
            # 原团队完成当前批次：分片调度仍显示原团队执行，计划页展示新归属与交接说明。
            note = f"计划归属由 {old_team} 转移至 {new_team}，当前批次 {batch['批次号']} 仍由 {old_team} 执行完成后交接"
            batch["交接说明"] = note
            _append_history(entry, "归属转移", note)
        elif active and mode == MODE_TAKEOVER_BY_TARGET:
            # 新团队接管当前批次：分片调度显示新执行团队，断点历史留下责任交接记录。
            batch["执行团队"] = new_team
            note = f"责任交接：批次 {batch['批次号']} 由 {old_team} 移交 {new_team} 接管执行"
            batch["交接说明"] = note
            _append_history(entry, "责任交接", note)
        else:
            _append_history(entry, "归属转移", f"计划归属由 {old_team} 转移至 {new_team}，当前无占用中批次")
        if active:
            _bind_shard_range(entry, batch)
        return entry, f"采集计划归属已由 {old_team} 转移至 {new_team}（{mode}）"

    # ------------------------------------------------------------------ 读取
    def _current_batch(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        batch_no = str(entry.get("续采批次", "") or "")
        if not batch_no:
            return None
        for row in _occupations():
            if row.get("批次号") == batch_no:
                return row
        return None

    def batch_of(self, batch_no: str) -> dict[str, Any] | None:
        for row in _occupations():
            if row.get("批次号") == batch_no:
                return row
        return None

    def summary(self) -> list[dict[str, Any]]:
        """断点计划归属汇总清单：由续采占用结果驱动，供分片调度页读取。"""
        rows: list[dict[str, Any]] = []
        for batch in _occupations():
            owner = batch.get("授予团队", "")
            for entry in store.rows(RESUME_TABLE):
                if entry.get("断点编号") == batch.get("断点编号"):
                    owner = entry.get("所属团队", owner)
                    break
            rows.append({
                "采集计划": batch.get("采集计划", ""),
                "断点编号": batch.get("断点编号", ""),
                "断点所属团队": owner,
                "唯一续采批次": batch.get("批次号", ""),
                "执行团队": batch.get("执行团队", ""),
                "分片范围": batch.get("占用范围", ""),
                "处理状态": batch.get("占用状态", ""),
                "原团队依据": batch.get("授予团队", ""),
                "交接说明": batch.get("交接说明", ""),
            })
        return rows

    def todos(self) -> list[dict[str, Any]]:
        """断点计划归属待办：其余业务入口改动断点或占用结果后这里同步更新。"""
        items: list[dict[str, Any]] = []
        for entry in store.rows(RESUME_TABLE):
            batch = self._current_batch(entry)
            active = batch is not None and batch.get("占用状态") == BATCH_ACTIVE
            failed = batch is not None and batch.get("占用状态") == BATCH_FAILED
            if failed:
                items.append({
                    "待办类型": "续采批次待重试",
                    "采集计划": entry.get("采集计划", ""),
                    "断点编号": entry.get("断点编号", ""),
                    "所属团队": entry.get("所属团队", ""),
                    "说明": f"批次 {batch.get('批次号')} 已失败，重试将复用原恢复位置",
                })
            elif active and batch.get("执行团队") != entry.get("所属团队"):
                items.append({
                    "待办类型": "归属交接进行中",
                    "采集计划": entry.get("采集计划", ""),
                    "断点编号": entry.get("断点编号", ""),
                    "所属团队": entry.get("所属团队", ""),
                    "说明": batch.get("交接说明", "批次执行团队与计划归属不一致"),
                })
            elif not active and entry.get("status") == "已记录" and _to_int(entry.get("剩余量")) > 0:
                items.append({
                    "待办类型": "待授予续采批次",
                    "采集计划": entry.get("采集计划", ""),
                    "断点编号": entry.get("断点编号", ""),
                    "所属团队": entry.get("所属团队", ""),
                    "说明": f"断点位置 {entry.get('断点位置', '')} 剩余 {entry.get('剩余量', '')} 待续采",
                })
        return items


occupation_service = OccupationService()
