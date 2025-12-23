import streamlit as st
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
import uuid

TZ = ZoneInfo("Asia/Taipei")

# ============================
# 固定規則（拿掉側欄調整）
# ============================
IMPORTANCE_THRESHOLD = 4   # 重要性 >=4 視為重要
URGENT_DAYS = 1            # 截止日 <= 明天 視為急
BUFFER_RATIO = 0.20        # 排程保留 20% 緩衝
ENSURE_Q2 = 1              # 至少先排 1 個 Q2


# ----------------------------
# Core logic
# ----------------------------
def compute_quadrant(task, tomorrow,
                     importance_threshold=IMPORTANCE_THRESHOLD,
                     urgent_days=URGENT_DAYS):
    """
    固定版本：
    - important: importance >= IMPORTANCE_THRESHOLD
    - urgent: due_date <= tomorrow + (urgent_days-1)
    """
    important = task["importance"] >= importance_threshold

    due = task["due"]
    if due is None:
        urgent = False
    else:
        urgent_limit = tomorrow + timedelta(days=max(urgent_days - 1, 0))
        urgent = due <= urgent_limit

    if important and urgent:
        return "Q1 重要且急"
    if important and not urgent:
        return "Q2 重要不急"
    if (not important) and urgent:
        return "Q3 不重要但急"
    return "Q4 不重要不急"


def minutes_between(a_dt, b_dt):
    return int((b_dt - a_dt).total_seconds() // 60)


def dt_on(day: date, t: time):
    return datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=TZ)


def generate_schedule(tasks, tomorrow, blocks,
                      importance_threshold=IMPORTANCE_THRESHOLD,
                      urgent_days=URGENT_DAYS,
                      buffer_ratio=BUFFER_RATIO,
                      ensure_q2=ENSURE_Q2):
    """
    固定版排程策略：
    - 只排 todo
    - 先排 Q1，再保證至少排 ensure_q2 個 Q2，接著 Q2、Q3、Q4
    - 留 buffer_ratio 緩衝
    - 排不下的列 overflow
    """
    todo = [t for t in tasks if t["status"] == "todo"]
    if not todo:
        return [], {}, {}, []

    # 可用時間段
    segments = []
    for (s_t, e_t) in blocks:
        s_dt = dt_on(tomorrow, s_t)
        e_dt = dt_on(tomorrow, e_t)
        if e_dt > s_dt:
            segments.append((s_dt, e_dt))
    if not segments:
        return [], {}, {}, todo

    total_available = sum(minutes_between(s, e) for s, e in segments)
    sched_limit = int(total_available * (1.0 - max(0.0, min(buffer_ratio, 0.8))))

    # 分類 + 排序 key
    enriched = []
    for t in todo:
        q = compute_quadrant(t, tomorrow, importance_threshold, urgent_days)
        due_key = t["due"].toordinal() if t["due"] else 10**9
        enriched.append((t, q, due_key))

    # 拆四群
    q1 = [(t, q, d) for (t, q, d) in enriched if q.startswith("Q1")]
    q2 = [(t, q, d) for (t, q, d) in enriched if q.startswith("Q2")]
    q3 = [(t, q, d) for (t, q, d) in enriched if q.startswith("Q3")]
    q4 = [(t, q, d) for (t, q, d) in enriched if q.startswith("Q4")]

    # 排序：截止越近越前、重要性高越前
    q1.sort(key=lambda x: (x[2], -x[0]["importance"]))
    q2.sort(key=lambda x: (x[2], -x[0]["importance"]))
    q3.sort(key=lambda x: (x[2], -x[0]["importance"]))
    q4.sort(key=lambda x: (-x[0]["importance"], x[2]))

    # 保證先排幾個 Q2
    q2_early = q2[:max(0, ensure_q2)]
    q2_rest = q2[max(0, ensure_q2):]
    ordered = q1 + q2_early + q2_rest + q3 + q4

    # 實際塞進時間段
    used = 0
    seg_idx = 0
    cursor = segments[0][0]

    def move_cursor(si, cur):
        while si < len(segments):
            s, e = segments[si]
            if cur < s:
                cur = s
            if cur < e:
                return si, cur
            si += 1
            if si < len(segments):
                cur = segments[si][0]
        return si, cur

    seg_idx, cursor = move_cursor(seg_idx, cursor)

    schedule = []
    overflow = []

    for (t, q, _) in ordered:
        dur = int(t["duration_min"])

        if used + dur > sched_limit:
            overflow.append(t)
            continue

        placed = False
        while seg_idx < len(segments):
            seg_idx, cursor = move_cursor(seg_idx, cursor)
            if seg_idx >= len(segments):
                break

            s, e = segments[seg_idx]
            remaining = minutes_between(cursor, e)
            if remaining <= 0:
                seg_idx += 1
                continue

            if dur <= remaining:
                start = cursor
                end = cursor + timedelta(minutes=dur)
                cursor = end
                used += dur
                schedule.append({
                    "start": start,
                    "end": end,
                    "title": t["title"],
                    "quadrant": q,
                    "task_id": t["id"],
                })
                placed = True
                break
            else:
                seg_idx += 1

        if not placed:
            overflow.append(t)

    # 四象限清單
    quad_map = {"Q1 重要且急": [], "Q2 重要不急": [], "Q3 不重要但急": [], "Q4 不重要不急": []}
    for (t, q, _) in enriched:
        quad_map[q].append(t)

    meta = {
        "total_available_min": total_available,
        "sched_limit_min": sched_limit,
        "used_min": used
    }
    return schedule, quad_map, meta, overflow


# ----------------------------
# UI state
# ----------------------------
st.set_page_config(page_title="To Do List", layout="wide")

if "tasks" not in st.session_state:
    st.session_state.tasks = []

today = datetime.now(TZ).date()
tomorrow = today + timedelta(days=1)

st.title("To Do List")
st.caption("行程推薦")


# ----------------------------
# Sidebar: 只留「可用時間」+ 清空/範例
# ----------------------------
with st.sidebar:
    st.subheader("明天可用時間")
    st.caption("彈性調整")

    en1 = st.checkbox("早段", True)
    s1 = st.time_input("早段開始", time(9, 0))
    e1 = st.time_input("早段結束", time(12, 0))

    en2 = st.checkbox("午段", True)
    s2 = st.time_input("午段開始", time(13, 30))
    e2 = st.time_input("午段結束", time(18, 0))

    en3 = st.checkbox("晚段", True)
    s3 = st.time_input("晚段開始", time(20, 0))
    e3 = st.time_input("晚段結束", time(22, 0))

    blocks = []
    if en1: blocks.append((s1, e1))
    if en2: blocks.append((s2, e2))
    if en3: blocks.append((s3, e3))

    st.divider()

    if st.button("🧹 清空所有任務", use_container_width=True):
        st.session_state.tasks = []
        st.success("已清空。")

    if st.button("✨ 填入範例任務", use_container_width=True):
        st.session_state.tasks.extend([
            {"id": str(uuid.uuid4()), "title": "把明天最重要的一件事做 60 分鐘", "duration_min": 60, "importance": 5, "due": None, "status": "todo"},
            {"id": str(uuid.uuid4()), "title": "回覆兩封信", "duration_min": 30, "importance": 3, "due": tomorrow, "status": "todo"},
            {"id": str(uuid.uuid4()), "title": "整理桌面/雜事", "duration_min": 30, "importance": 2, "due": None, "status": "todo"},
        ])
        st.success("已加入範例。")

    st.divider()
    st.caption("固定規則：重要>=4、急=截止<=明天、緩衝20%、先排1個Q2")


# ----------------------------
# Main: Input + List + Plan
# ----------------------------
c1, c2 = st.columns([1, 1])

with c1:
    st.subheader("① 睡前輸入（30 秒）")
    with st.form("add_task", clear_on_submit=True):
        title = st.text_input("任務", placeholder="例：寫論文 60 分鐘 / 運動 30 分鐘…")
        duration_min = st.selectbox("預估時間(分)", [15, 30, 45, 60, 90, 120], index=1)
        importance = st.slider("重要性(1~5)", 1, 5, 3)

        due_opt = st.selectbox("截止日", ["無", "明天", "自選日期"], index=0)
        due = None
        if due_opt == "明天":
            due = tomorrow
        elif due_opt == "自選日期":
            due = st.date_input("選日期", value=tomorrow)

        add = st.form_submit_button("➕ 加入")
        if add:
            if not title.strip():
                st.error("任務不能空白。")
            else:
                st.session_state.tasks.append({
                    "id": str(uuid.uuid4()),
                    "title": title.strip(),
                    "duration_min": int(duration_min),
                    "importance": int(importance),
                    "due": due,
                    "status": "todo",
                })
                st.success("已加入！")

with c2:
    st.subheader("② 任務清單")
    tasks = st.session_state.tasks

    if not tasks:
        st.info("目前沒有任務。先在左邊新增。")
    else:
        table = []
        for t in tasks:
            table.append({
                "任務": t["title"],
                "時間(分)": t["duration_min"],
                "重要性": t["importance"],
                "截止日": t["due"].isoformat() if t["due"] else "",
                "狀態": t["status"],
                "id": t["id"],
            })

        st.dataframe(
            [{k: v for k, v in row.items() if k != "id"} for row in table],
            use_container_width=True,
            hide_index=True,
        )

        ids = [row["id"] for row in table]
        pick = st.selectbox(
            "選要刪的任務",
            ids,
            format_func=lambda x: next(r["任務"] for r in table if r["id"] == x),
        )
        if st.button("🗑️ 刪除選取任務", use_container_width=True):
            st.session_state.tasks = [t for t in st.session_state.tasks if t["id"] != pick]
            st.success("已刪除。")

st.divider()

st.subheader("③ 一鍵生成：明天行程")
gen = st.button("🚀 產生明日行程", use_container_width=True)

tasks = st.session_state.tasks
todo = [t for t in tasks if t["status"] == "todo"]

# 四象限顯示（固定規則）
quad_now = {"Q1 重要且急": [], "Q2 重要不急": [], "Q3 不重要但急": [], "Q4 不重要不急": []}
for t in todo:
    q = compute_quadrant(t, tomorrow)
    quad_now[q].append(t)

qcol1, qcol2, qcol3, qcol4 = st.columns(4)
for col, qname in zip([qcol1, qcol2, qcol3, qcol4], quad_now.keys()):
    with col:
        st.markdown(f"### {qname}")
        if not quad_now[qname]:
            st.caption("（空）")
        else:
            for t in quad_now[qname]:
                st.write(f"• {t['title']} ({t['duration_min']}m)")

if gen:
    schedule, quad_map, meta, overflow = generate_schedule(
        tasks=tasks,
        tomorrow=tomorrow,
        blocks=blocks,
    )

    st.divider()
    st.markdown(f"### 🗓️ 明日時間表（{tomorrow.isoformat()}）")

    if not schedule:
        st.warning("排不出時間表：可能是你沒設定可用時間段，或沒有待辦。")
    else:
        rows = []
        for it in schedule:
            rows.append({
                "開始": it["start"].strftime("%H:%M"),
                "結束": it["end"].strftime("%H:%M"),
                "任務": it["title"],
                "象限": it["quadrant"],
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

        st.caption(
            f"可用 {meta['total_available_min']} 分｜實排上限 {meta['sched_limit_min']} 分（固定緩衝 {int(BUFFER_RATIO*100)}%）｜已排 {meta['used_min']} 分"
        )

    if overflow:
        st.markdown("### ⛔ 排不下（自動延後）")
        for t in overflow:
            st.write(f"• {t['title']} ({t['duration_min']}m)")

    plan_lines = [f"明日行程 {tomorrow.isoformat()}"]
    for it in schedule:
        plan_lines.append(f"- {it['start'].strftime('%H:%M')}–{it['end'].strftime('%H:%M')} {it['title']} ({it['quadrant']})")
    if overflow:
        plan_lines.append("")
        plan_lines.append("排不下（延後）：")
        for t in overflow:
            plan_lines.append(f"- {t['title']} ({t['duration_min']}m)")
    st.text_area("📌 直接複製貼到筆記", "\n".join(plan_lines), height=220)
