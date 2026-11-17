"""断点续采占用仲裁的回归验证：直接用服务层跑，不依赖 HTTP 服务。

运行：cd backend && .venv/bin/python tests/test_resume_occupation.py
"""
from __future__ import annotations

import threading

from app.services.occupation import (
    MODE_FINISH_BY_SOURCE,
    MODE_TAKEOVER_BY_TARGET,
    occupation_service,
)
from app.services.resume import ResumeService
from app.services.shard import ShardService
from app.store import store

resume_service = ResumeService()
shard_service = ShardService()

PASSED = 0


def check(label: str, condition: bool) -> None:
    global PASSED
    assert condition, f"失败：{label}"
    PASSED += 1
    print(f"  通过：{label}")


def new_breakpoint(position: str, team: str, remaining: str = "1000") -> dict:
    entry, missing = resume_service.create_entry({
        "断点编号": f"RESU-T{position}",
        "采集计划": f"验证计划{position}",
        "断点位置": position,
        "剩余量": remaining,
        "所属团队": team,
    })
    assert not missing, f"登记失败：{missing}"
    return entry  # type: ignore[return-value]


def occupations_of(position: str) -> list[dict]:
    return [row for row in store.rows("resume_occupation") if row.get("断点位置") == position]


print("1. 授权启动续采并仲裁占用")
entry = new_breakpoint("90000", "采集一组")
batch, message, granted = occupation_service.grant(entry, "采集一组")
check("授予成功", granted and batch is not None)
check("占用范围按水位计算", batch["占用范围"] == "90000-91000")
check("断点记录带上唯一续采批次", entry["续采批次"] == batch["批次号"] and entry["占用状态"] == "占用中")
bound = [row for row in store.rows("shard") if row.get("续采批次") == batch["批次号"]]
check("分片调度绑定恢复批次的分片范围", len(bound) == 1 and bound[0]["分片范围"] == "90000-91000")
check("分片行显示断点所属团队与执行团队", bound[0]["断点所属团队"] == "采集一组" and bound[0]["执行团队"] == "采集一组")

print("2. 重复启动返回既有占用结果")
again, message2, granted2 = occupation_service.grant(entry, "采集一组")
check("重复启动未新授予", not granted2 and again is not None and again["批次号"] == batch["批次号"])
check("占用记录仍只有一份", len(occupations_of("90000")) == 1)
check("提示语说明返回既有占用结果", "既有占用结果" in message2)

print("3. 并发启动同一断点只授予一个续采批次")
race_entry = new_breakpoint("91000", "采集一组")
results: list[tuple] = []
threads = [threading.Thread(target=lambda: results.append(occupation_service.grant(race_entry, "采集一组"))) for _ in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("并发后占用记录只有一份", len(occupations_of("91000")) == 1)
check("并发结果都指向同一批次", len({r[0]["批次号"] for r in results if r[0]}) == 1)
check("并发中只有一方是新授予", sum(1 for r in results if r[2]) == 1)

print("4. 越权请求只能读取归属和处理状态")
denied, deny_message, deny_granted = occupation_service.grant(entry, "采集二组")
check("越权启动被拒绝", denied is None and not deny_granted)
check("越权提示只读", "仅可读取归属和处理状态" in deny_message)
view = resume_service.get_entry(entry["id"], team="采集二组")
check("越权明细只读", view.get("只读") is True)
check("越权视图不含断点历史", "断点历史" not in view and "剩余量" not in view)
check("越权视图包含归属和处理状态", view.get("所属团队") == "采集一组" and "处理状态" in view)
full = resume_service.get_entry(entry["id"], team="采集一组")
check("归属团队可读完整明细", "断点历史" in full)

print("5. 失败只释放本次未使用范围，断点水位不回退")
released, rel_message = occupation_service.release_unused(entry, "采集一组", 400)
check("失败释放成功", released is not None and released["占用状态"] == "已失败")
check("水位按已用量前进", entry["断点位置"] == "90400" and entry["已采集量"] == "90400")
check("剩余量扣减已用", entry["剩余量"] == "600")
check("未使用范围被释放", released["已释放范围"] == "90400-91000")
check("断点历史记录释放且水位不回退", any(h["事件"] == "失败释放" and "不回退" in h["说明"] for h in entry["断点历史"]))

print("6. 失败重试复用原批次，不占第二份恢复位置")
retry, retry_message, retry_granted = occupation_service.grant(entry, "采集一组")
check("重试复用既有批次", retry is not None and retry["批次号"] == batch["批次号"] and not retry_granted)
check("占用记录仍只有一份", len(occupations_of("90400")) == 0 and len(occupations_of("90000")) == 1)
check("重试后占用范围从当前水位起", retry["占用范围"] == "90400-91000")
check("提示语说明未占第二份恢复位置", "第二份恢复位置" in retry_message)

print("7. 完成批次推进水位")
done, done_message = occupation_service.complete(entry, "采集一组")
check("批次完成", done is not None and done["占用状态"] == "已完成")
check("水位推进到终点", entry["断点位置"] == "91000" and entry["剩余量"] == "0")
check("断点状态已恢复", entry["status"] == "已恢复")
shard_row = [row for row in store.rows("shard") if row.get("续采批次") == batch["批次号"]][0]
check("分片行同步完成", shard_row["status"] == "已完成")

print("8. 归属转移方式一：原团队完成当前批次")
entry_b = new_breakpoint("92000", "采集一组", "500")
batch_b, _, _ = occupation_service.grant(entry_b, "采集一组")
result, transfer_message = occupation_service.transfer(
    entry_b, new_team="采集二组", mode=MODE_FINISH_BY_SOURCE, operator_team="采集一组",
)
check("归属转移成功", result is not None and entry_b["所属团队"] == "采集二组")
check("当前批次执行团队仍是原团队", batch_b["执行团队"] == "采集一组")
check("批次保留原团队依据", batch_b["授予团队"] == "采集一组")
check("交接说明已生成", "执行完成后交接" in batch_b["交接说明"])
summary_row = [row for row in shard_service.resume_summary() if row["唯一续采批次"] == batch_b["批次号"]][0]
check("汇总清单显示新归属", summary_row["断点所属团队"] == "采集二组")
check("汇总清单显示原团队执行", summary_row["执行团队"] == "采集一组")
check("汇总清单带交接说明", "交接" in summary_row["交接说明"])
shard_b = [row for row in store.rows("shard") if row.get("续采批次") == batch_b["批次号"]][0]
check("分片调度显示执行团队为原团队", shard_b["执行团队"] == "采集一组" and shard_b["断点所属团队"] == "采集二组")
check("交接进行中进入待办", any(t["待办类型"] == "归属交接进行中" and t["断点编号"] == entry_b["断点编号"] for t in shard_service.resume_todos()))
done_b, msg_b = occupation_service.complete(entry_b, "采集一组")
check("方式一中原团队可把当前批次执行完", done_b is not None and batch_b["占用状态"] == "已完成")

print("9. 归属转移方式二：新团队接管当前批次")
entry_c = new_breakpoint("93000", "采集一组", "500")
batch_c, _, _ = occupation_service.grant(entry_c, "采集一组")
occupation_service.transfer(
    entry_c, new_team="采集三组", mode=MODE_TAKEOVER_BY_TARGET, operator_team="采集一组",
)
check("执行团队切换为新团队", batch_c["执行团队"] == "采集三组")
check("断点历史出现责任交接记录", any(h["事件"] == "责任交接" for h in entry_c["断点历史"]))
shard_c = [row for row in store.rows("shard") if row.get("续采批次") == batch_c["批次号"]][0]
check("分片调度显示新执行团队", shard_c["执行团队"] == "采集三组")
check("接管批次仍保留原团队依据", batch_c["授予团队"] == "采集一组")

print("10. 权限变更后旧批次保留原团队依据")
occupation_service.complete(entry_c, "采集三组")
check("新团队可完成接管的批次", batch_c["占用状态"] == "已完成")
check("完成后旧批次仍记录授予团队", batch_c["授予团队"] == "采集一组")

print("11. 归属待办与汇总清单随业务入口更新")
todos = shard_service.resume_todos()
check("待授予续采批次进入待办", any(t["待办类型"] == "待授予续采批次" for t in todos))
entry_d = new_breakpoint("94000", "采集一组", "300")
check("登记断点后待办同步新增", any(t["断点编号"] == entry_d["断点编号"] for t in shard_service.resume_todos()))
check("汇总清单覆盖全部占用结果", len(shard_service.resume_summary()) == len(store.rows("resume_occupation")))

print("12. 越权转移与越权释放被拦截")
result_x, message_x = occupation_service.transfer(
    entry_b, new_team="采集三组", mode=MODE_TAKEOVER_BY_TARGET, operator_team="采集九组",
)
check("越权转移被拒绝", result_x is None and "越权" in message_x)
rel_x, message_rx = occupation_service.release_unused(entry_b, "采集九组", 0)
check("越权释放被拒绝", rel_x is None and "越权" in message_rx)

print(f"\n全部 {PASSED} 项检查通过")
