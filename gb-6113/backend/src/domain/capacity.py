"""带容量入场与必保插队的离散时间模拟。

规则（交班前集中写入、主机来不及答复的场景）：

- 每份剧本可设容量：入场凭条数达到容量后，新到凭条先排队，等收到答复腾出空位。
- 必保凭条（fallback 为假）可越过排队中的普通凭条；同类凭条按到达顺序入场。
- 已入场凭条按入场顺序串行落盘（本机 -> 备机），即按入场顺序驱动顺序复制。
- 同一时刻先处理答复、再分配空位；落盘/答复时点等于故障时刻按已发生处理。
- 可降级凭条在本机落盘后等待降级时限可本地答复；降级答复后副本照常补写。
- 故障时输出三类凭条：仍在排队、已入场未答复、已答复但目标备机未落盘。
"""

import heapq
from types import SimpleNamespace

from .flush import finish_at

LOCAL_DONE = 1
REPLY = 2
ARRIVAL = 3

STATUS_QUEUED = "故障时仍在排队"
STATUS_ADMITTED = "已入场未答复"
STATUS_MISSING = "已答复但目标缺失"
STATUS_RECOVERABLE = "已答复且目标可恢复"
STATUS_LATE = "故障后到达未入场"


def _pop_waiting(waiting):
    """必保凭条越过普通凭条；同类内部按到达顺序（序号小者先入场）。"""
    best = 0
    for index in range(1, len(waiting)):
        current = waiting[index]
        chosen = waiting[best]
        if current.urgent > chosen.urgent or (
            current.urgent == chosen.urgent and current.seq < chosen.seq
        ):
            best = index
    return waiting.pop(best)


def _waiting_insert_position(waiting, urgent):
    """队列保持必保在前、同类按到达顺序；新到凭条追加在同优先级末尾。"""
    index = 0
    while index < len(waiting) and waiting[index].urgent >= urgent:
        index += 1
    return index


def simulate(writes, replicas, quorum, crash, promoted, timeout, capacity):
    # writes 已按到达时间排序，下标即稳定的到达序号，用于同类排序与平局裁决。
    events = []
    for seq, write in enumerate(writes):
        if write.arrival <= crash:
            heapq.heappush(events, (write.arrival, ARRIVAL, seq, write))

    waiting = []          # 排队中的凭条，必保在前、同类按到达顺序
    occupied = 0          # 当前占用的入场空位
    admitted_order = []   # 已入场凭条，按入场顺序
    local_free = 0        # 本机串行落盘空闲时点
    remote_free = {item.name: 0 for item in replicas}
    metrics = {STATUS_QUEUED: 0, STATUS_ADMITTED: 0, STATUS_MISSING: 0}

    def admit(entry, now):
        nonlocal occupied, local_free
        occupied += 1
        admitted_order.append(entry)
        entry.admitted_at = now
        # 已入场凭条按入场顺序串行占用本机。
        local_start = max(local_free, now)
        entry.local_at = local_start + entry.write.local_ms
        local_free = entry.local_at
        heapq.heappush(events, (entry.local_at, LOCAL_DONE, entry.seq, entry))

    def start_replicas(entry):
        # 本机落盘后各备机按顺序复制消耗各自的服务时间，中断窗内暂停。
        adjusted = {}
        for item in replicas:
            earliest = max(entry.local_at, remote_free[item.name])
            adjusted[item.name] = finish_at(earliest, item.flush_ms, item.outages)
        remote_free.update(adjusted)
        entry.remote = adjusted
        entry.strict_at = sorted(adjusted.values())[quorum - 1]
        entry.cutoff = (
            min(entry.strict_at, entry.local_at + timeout)
            if entry.write.fallback else entry.strict_at
        )
        # 降级答复先到时副本不取消，照常补写至各自落盘时点。
        heapq.heappush(events, (entry.cutoff, REPLY, entry.seq, entry))

    def release(entry, now):
        # 收到答复才腾出空位；同一时刻先答复、再由 fill 分配空位。
        nonlocal occupied
        entry.responded = True
        entry.response_at = now
        occupied -= 1

    def fill(now):
        while capacity is not None and occupied < capacity and waiting:
            admit(_pop_waiting(waiting), now)

    while events:
        now = events[0][0]
        if now > crash:
            break
        current = []
        while events and events[0][0] == now:
            current.append(heapq.heappop(events))
        # 同一时刻先处理答复，再处理本机落盘完成与到达，最后统一分配空位。
        current.sort(key=lambda event: event[1])
        for _, kind, seq, item in current:
            if kind == REPLY:
                release(item, now)
            elif kind == LOCAL_DONE:
                start_replicas(item)
            else:
                entry = SimpleNamespace(
                    write=item, seq=seq, urgent=not item.fallback,
                    admitted_at=None, local_at=None, remote=None, strict_at=None,
                    cutoff=None, responded=False, response_at=None,
                )
                waiting.insert(_waiting_insert_position(waiting, entry.urgent), entry)
        fill(now)

    by_code = {entry.write.code: entry for entry in [*admitted_order, *waiting]}
    rows = [_row(write, by_code.get(write.code), crash, promoted, metrics) for write in writes]
    return rows, metrics


def _row(write, entry, crash, promoted, metrics):
    if entry is None:
        return _base_row(write, None, None, None, None, STATUS_LATE, None, None)

    admitted = entry.admitted_at if entry.admitted_at is not None else None
    local_at = entry.local_at if entry.local_at is not None and entry.local_at <= crash else None

    if not entry.responded:
        if admitted is None:
            status = STATUS_QUEUED
            metrics[STATUS_QUEUED] += 1
        else:
            status = STATUS_ADMITTED
            metrics[STATUS_ADMITTED] += 1
        return _base_row(write, admitted, local_at, None, None, status, None, None)

    response_at = entry.response_at
    strict_at = entry.strict_at if entry.strict_at <= crash else None
    path = "本地降级" if entry.write.fallback and entry.cutoff < entry.strict_at else "同步确认"
    target_at = entry.remote[promoted]
    if target_at <= crash:
        status = STATUS_RECOVERABLE
        target_value = target_at
    else:
        status = STATUS_MISSING
        metrics[STATUS_MISSING] += 1
        target_value = None
    return _base_row(write, admitted, local_at, response_at, strict_at, status, target_value, path)


def _base_row(write, admitted, local_at, response_at, strict_at, status, target_at, path):
    return {
        "写入号": write.code,
        "雪道": write.area,
        "必保": "否" if write.fallback else "是",
        "到达 ms": write.arrival,
        "入场 ms": admitted,
        "本机落盘 ms": local_at,
        "严格答复 ms": strict_at,
        "按策略答复 ms": response_at,
        "答复路径": path,
        "目标落盘 ms": target_at,
        "故障时状态": status,
    }
