"""Simple local dashboard renderer for Slouchy stats."""

from __future__ import annotations

import datetime
import html
from typing import Any


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    rem = minutes % 60
    return f"{hours}h {rem}m"


def _tier_label(tier: int) -> str:
    labels = {
        1: "Gentle reminder",
        2: "Strong reminder",
        3: "Urgent alert",
    }
    return labels.get(tier, f"Reminder ({tier})")


def _render_hourly_chart(hourly_data: list[dict[str, Any]]) -> str:
    """Return an inline SVG bar chart of per-hour posture scores."""
    chart_w = 960
    chart_h = 120
    label_h = 30
    bar_unit = chart_w / 24
    bar_w = bar_unit - 2

    parts: list[str] = []

    # Faint guide lines at 50% and 100% height
    parts.append(
        f'<line x1="0" y1="0" x2="{chart_w}" y2="0" stroke="#d9d0bf" stroke-width="1"/>'
    )
    parts.append(
        f'<line x1="0" y1="{chart_h * 0.5:.1f}" x2="{chart_w}" y2="{chart_h * 0.5:.1f}"'
        f' stroke="#d9d0bf" stroke-width="0.5" stroke-dasharray="4,4"/>'
    )
    parts.append(
        f'<line x1="0" y1="{chart_h}" x2="{chart_w}" y2="{chart_h}" stroke="#d9d0bf" stroke-width="1"/>'
    )

    for item in hourly_data:
        h = item["hour"]
        score = float(item["posture_score"])
        monitoring = float(item["monitoring_seconds"])
        absence = float(item.get("absence_seconds", 0.0))
        present = float(item.get("present_seconds", monitoring))
        events = int(item["event_count"])
        x = h * bar_unit + 1

        is_absent = monitoring > 30 and present < 60
        if is_absent:
            # Monitored but no one detected — show a small gray stub
            stub_h = chart_h * 0.28
            stub_y = chart_h - stub_h
            abs_min = int(absence / 60)
            label = f"{h:02d}:00 — Absent ({abs_min}m undetected)"
            parts.append(
                f'<rect x="{x:.1f}" y="{stub_y:.1f}" width="{bar_w:.1f}" height="{stub_h:.1f}"'
                f' fill="#b4aba0" rx="2" opacity="0.75"><title>{html.escape(label)}</title></rect>'
            )
        elif present > 0:
            bh = max(2.0, (score / 100.0) * chart_h)
            by = chart_h - bh
            fill = "#2e8b57" if score >= 90 else ("#b5751d" if score >= 70 else "#a33a2f")
            label = f"{h:02d}:00 — {score:.0f}% posture, {events} event{'s' if events != 1 else ''}"
            parts.append(
                f'<rect x="{x:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}"'
                f' fill="{fill}" rx="2"><title>{html.escape(label)}</title></rect>'
            )
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="0" width="{bar_w:.1f}" height="{chart_h}"'
                f' fill="#e9dfcc" rx="2" opacity="0.4"/>'
            )

        if h % 3 == 0:
            hour_12 = h % 12 or 12
            ampm = "am" if h < 12 else "pm"
            lx = x + bar_unit / 2 - 1
            parts.append(
                f'<text x="{lx:.1f}" y="{chart_h + 20}" text-anchor="middle"'
                f' font-size="11" fill="#6b777a">{hour_12}{ampm}</text>'
            )

    total_h = chart_h + label_h
    inner = "\n    ".join(parts)
    return (
        f'<svg viewBox="0 0 {chart_w} {total_h}" xmlns="http://www.w3.org/2000/svg"'
        f' style="width:100%;display:block;">\n  <g>\n    {inner}\n  </g>\n</svg>'
    )


def render_dashboard_html(
    today_summary: dict[str, Any],
    recent_days: list[dict[str, Any]],
    recent_events: list[dict[str, Any]],
    today_hourly: list[dict[str, Any]] | None = None,
) -> str:
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_iso = datetime.date.today().isoformat()

    def _has_activity(day: dict[str, Any]) -> bool:
        return (
            float(day.get("monitoring_seconds", 0.0)) > 0.0
            or float(day.get("slouch_seconds", 0.0)) > 0.0
            or int(day.get("event_count", 0)) > 0
        )

    active_days = [day for day in recent_days if _has_activity(day)]
    historical_days = [
        day for day in recent_days if day.get("date") != today_iso and _has_activity(day)
    ]

    day_rows = []
    for day in historical_days:
        day_rows.append(
            f"""
            <tr>
              <td>{html.escape(day["date"])}</td>
              <td>{_fmt_duration(day["monitoring_seconds"])}</td>
              <td>{_fmt_duration(day["slouch_seconds"])}</td>
              <td>{int(day["event_count"])}</td>
              <td>{day["posture_score"]:.1f}%</td>
            </tr>
            """
        )

    event_rows = []
    for evt in recent_events[:100]:
        dt = datetime.datetime.fromtimestamp(float(evt["timestamp"])).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        event_rows.append(
            f"""
            <tr>
              <td>{html.escape(dt)}</td>
              <td>{_fmt_duration(float(evt["duration"]))}</td>
              <td>{html.escape(_tier_label(int(evt["tier"])))}</td>
            </tr>
            """
        )

    total_days = len(active_days)
    avg_score = (
        sum(day["posture_score"] for day in active_days) / total_days if total_days else None
    )

    today_monitor_min = float(today_summary["total_monitoring_minutes"])
    today_slouch_min = float(today_summary["total_slouch_minutes"])
    today_good_min = max(0.0, today_monitor_min - today_slouch_min)
    today_events = int(today_summary["slouch_events"])

    # Reward mechanics (simple, transparent, and easy to tune).
    daily_goal_min = 120.0
    goal_pct = min(100.0, (today_good_min / daily_goal_min) * 100.0) if daily_goal_min > 0 else 0.0
    reward_points = max(0, int(today_good_min * 2) - (today_events * 5))

    # Consecutive "clean" days: at least 30 min monitored and zero slouch events.
    clean_streak_days = 0
    for day in reversed(recent_days):
        monitored_min = float(day.get("monitoring_seconds", 0.0)) / 60.0
        event_count = int(day.get("event_count", 0))
        if monitored_min >= 30.0 and event_count == 0:
            clean_streak_days += 1
        else:
            break

    badges = []
    if today_events == 0 and today_monitor_min >= 30.0:
        badges.append("No-Slouch Hero")
    if float(today_summary["best_streak_minutes"]) >= 60.0:
        badges.append("Iron Spine")
    if avg_score is not None and avg_score >= 90.0 and total_days >= 5:
        badges.append("Consistency Champ")
    if clean_streak_days >= 3:
        badges.append("Streak Builder")
    if not badges:
        badges.append("Getting Started")

    chart_section = ""
    if today_hourly:
        chart_svg = _render_hourly_chart(today_hourly)
        chart_section = f"""
    <div class="section-title">Today&#8217;s Activity</div>
    <div class="chart-box">
      <div class="hint" style="margin-bottom:6px;">Posture score by hour (present time only) — green ≥90%, orange ≥70%, red &lt;70%, gray = absent</div>
      {chart_svg}
    </div>"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Slouchy Dashboard</title>
  <style>
    :root {{
      --bg: #f3efe5;
      --ink: #1f2a2e;
      --muted: #6b777a;
      --card: #fffaf2;
      --line: #d9d0bf;
      --good: #2e8b57;
      --warn: #b5751d;
      --bad: #a33a2f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: radial-gradient(circle at 20% -20%, #fff8e7, var(--bg));
      color: var(--ink);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
    }}
    .wrap {{
      max-width: 1000px;
      margin: 28px auto;
      padding: 0 18px 32px;
    }}
    h1 {{
      margin: 0;
      font-size: 30px;
      letter-spacing: 0.3px;
    }}
    .sub {{
      color: var(--muted);
      margin: 4px 0 20px;
      font-size: 14px;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 10px;
      margin-bottom: 16px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px;
    }}
    .k {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.8px;
    }}
    .v {{
      margin-top: 6px;
      font-size: 24px;
      font-weight: 700;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      overflow: hidden;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 14px;
    }}
    th {{
      color: var(--muted);
      font-weight: 600;
      background: #f7f2e8;
    }}
    tr:last-child td {{ border-bottom: none; }}
    .section-title {{
      margin: 18px 0 8px;
      font-size: 18px;
    }}
    .hint {{
      color: var(--muted);
      font-size: 13px;
    }}
    .reward-box {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px;
      margin-bottom: 12px;
    }}
    .reward-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 8px;
      margin-top: 8px;
    }}
    .reward-pill {{
      display: inline-block;
      background: #efe7d8;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 6px 10px;
      margin: 4px 6px 0 0;
      font-size: 13px;
      font-weight: 600;
    }}
    .meter {{
      margin-top: 8px;
      width: 100%;
      height: 12px;
      border-radius: 999px;
      background: #e9dfcc;
      overflow: hidden;
      border: 1px solid var(--line);
    }}
    .meter-fill {{
      height: 100%;
      width: {goal_pct:.1f}%;
      background: linear-gradient(90deg, #2e8b57, #75b97b);
    }}
    .chart-box {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px 8px;
      margin-bottom: 12px;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Slouchy Dashboard</h1>
    <div class="sub">Generated at {html.escape(generated_at)} (local time)</div>

    <div class="cards">
      <div class="card"><div class="k">Today's Monitoring</div><div class="v">{_fmt_duration(float(today_summary["total_monitoring_minutes"]) * 60)}</div></div>
      <div class="card"><div class="k">Today's Slouch</div><div class="v">{_fmt_duration(float(today_summary["total_slouch_minutes"]) * 60)}</div></div>
      <div class="card"><div class="k">Today's Events</div><div class="v">{int(today_summary["slouch_events"])}</div></div>
      <div class="card"><div class="k">Today's Score</div><div class="v">{float(today_summary["posture_score"]):.1f}%</div></div>
      <div class="card"><div class="k">Best Streak Today</div><div class="v">{_fmt_duration(float(today_summary["best_streak_minutes"]) * 60)}</div></div>
      <div class="card"><div class="k">14-Day Avg Score</div><div class="v">{f"{avg_score:.1f}%" if avg_score is not None else "N/A"}</div></div>
    </div>

    <div class="section-title">Rewards</div>
    <div class="reward-box">
      <div><strong>{reward_points} points</strong> earned today</div>
      <div class="hint">+2 per good minute, -5 per slouch event</div>
      <div class="reward-grid">
        <div><div class="k">Daily Goal</div><div class="v">{goal_pct:.0f}%</div></div>
        <div><div class="k">Good Minutes</div><div class="v">{today_good_min:.0f}m</div></div>
        <div><div class="k">Clean Streak</div><div class="v">{clean_streak_days} days</div></div>
      </div>
      <div class="meter"><div class="meter-fill"></div></div>
      <div style="margin-top:8px;">
        {"".join(f'<span class="reward-pill">{html.escape(b)}</span>' for b in badges)}
      </div>
    </div>

    {chart_section}

    <div class="section-title">History (Previous 14 Days)</div>
    <table>
      <thead>
        <tr>
          <th>Date</th><th>Monitoring</th><th>Slouch</th><th>Events</th><th>Score</th>
        </tr>
      </thead>
      <tbody>
        {"".join(day_rows) if day_rows else '<tr><td colspan="5">History is not available yet.</td></tr>'}
      </tbody>
    </table>

    <div class="section-title">Recent Slouch Events</div>
    <table>
      <thead>
        <tr>
          <th>Timestamp</th><th>Duration</th><th>Reminder</th>
        </tr>
      </thead>
      <tbody>
        {"".join(event_rows) if event_rows else '<tr><td colspan="3">No slouch events yet.</td></tr>'}
      </tbody>
    </table>
    <p class="hint">Score = max(0, monitoring - slouch) / monitoring, capped at 100%.</p>
  </div>
</body>
</html>
"""
