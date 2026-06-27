#!/usr/bin/env python3
"""
Analyze staged conversation latency and generate review-friendly reports.

Input formats:
- JSONL events: one JSON object per line with conversation_id, phase, timestamp,
  and optional event=start|end.
- CSV events: same field names as the JSONL format.
- Aggregated rows: include duration_ms or duration_sec directly.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_PHASE_ORDER = [
    "wake",
    "record",
    "upload",
    "asr",
    "llm",
    "tts",
    "download",
    "playback",
    "eyes",
]

OVERLAP_PHASES = {"first_response"}


@dataclass(frozen=True)
class Segment:
    conversation_id: str
    phase: str
    start_ms: float | None
    end_ms: float | None
    duration_ms: float
    source_line: int


def parse_timestamp_ms(value: Any) -> float:
    if value is None or value == "":
        raise ValueError("missing timestamp")
    if isinstance(value, (int, float)):
        # Treat very large numbers as epoch milliseconds, otherwise seconds.
        return float(value if value > 10_000_000_000 else value * 1000)

    text = str(value).strip()
    if text.isdigit():
        number = float(text)
        return number if number > 10_000_000_000 else number * 1000

    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() * 1000


def read_rows(path: Path) -> list[tuple[int, dict[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows: list[tuple[int, dict[str, Any]]] = []
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                rows.append((line_no, json.loads(line)))
        return rows

    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return [(line_no, dict(row)) for line_no, row in enumerate(csv.DictReader(fh), 2)]

    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("JSON input must be an array of row objects")
        return [(line_no, row) for line_no, row in enumerate(data, 1)]

    if suffix in {".log", ".out", ".txt"}:
        return read_conversation_metrics_log(path)

    raise ValueError("input must be .jsonl, .json, .csv, .log, .out, or .txt")


def read_conversation_metrics_log(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    line_re = re.compile(r"\[conversation_metrics\]\s+id=(?P<id>\S+)\s+total=(?P<total>[0-9.]+)ms\b")
    first_response_re = re.compile(r"\bfirst_response_ms=(?P<value>[0-9.]+|-)\b")
    step_re = re.compile(r"(?P<phase>[a-zA-Z0-9_]+)\s+\+(?P<delta>[0-9.]+)ms\s*/\s*(?P<elapsed>[0-9.]+)ms")

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line_no, line in enumerate(fh, 1):
            if "[conversation_metrics]" not in line:
                continue
            match = line_re.search(line)
            if not match:
                continue
            conversation_id = match.group("id")
            total_ms = float(match.group("total"))
            if total_ms <= 0 or "|" not in line:
                continue
            timeline = line.split("|", 1)[1]
            occurrence_count: dict[str, int] = defaultdict(int)
            first_response_match = first_response_re.search(line)
            first_response_ms = (
                float(first_response_match.group("value"))
                if first_response_match and first_response_match.group("value") != "-"
                else None
            )
            if first_response_ms is not None:
                rows.append(
                    (
                        line_no,
                        {
                            "conversation_id": conversation_id,
                            "phase": "first_response",
                            "duration_ms": first_response_ms,
                            "source": "conversation_metrics",
                        },
                    )
                )
            saw_first_response = first_response_ms is not None
            for step in timeline.split("->"):
                step_match = step_re.search(step)
                if not step_match:
                    continue
                phase = step_match.group("phase")
                elapsed_ms = float(step_match.group("elapsed"))
                if phase == "first_audio_sent" and not saw_first_response:
                    rows.append(
                        (
                            line_no,
                            {
                                "conversation_id": conversation_id,
                                "phase": "first_response",
                                "duration_ms": elapsed_ms,
                                "source": "conversation_metrics",
                            },
                        )
                    )
                    saw_first_response = True
                occurrence_count[phase] += 1
                rows.append(
                    (
                        line_no,
                        {
                            "conversation_id": conversation_id,
                            "phase": normalize_metrics_phase(phase),
                            "duration_ms": float(step_match.group("delta")),
                            "elapsed_ms": elapsed_ms,
                            "occurrence": occurrence_count[phase],
                            "source": "conversation_metrics",
                        },
                    )
                )
    return rows


def normalize_metrics_phase(phase: str) -> str:
    mapping = {
        "asr_final": "asr",
        "intent_start": "intent",
        "intent_done": "intent",
        "stt_message_sent": "device_message",
        "chat_submit": "chat_queue",
        "chat_start": "chat_queue",
        "memory_start": "memory",
        "memory_done": "memory",
        "llm_request_ready": "llm_prepare",
        "llm_first_token": "llm_first_token",
        "llm_done": "llm_stream",
        "emotion_ws_sent": "eyes",
        "tts_segment_start": "tts_queue",
        "tts_segment_generated": "tts_generate",
        "tts_sentence_start": "audio_queue",
        "first_audio_sent": "first_audio_send",
        "tts_stop": "playback_drain",
    }
    return mapping.get(phase, phase)


def row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def rows_to_segments(rows: Iterable[tuple[int, dict[str, Any]]]) -> list[Segment]:
    segments: list[Segment] = []
    open_starts: dict[tuple[str, str], tuple[float, int]] = {}

    for line_no, row in rows:
        conversation_id = str(row_value(row, "conversation_id", "conversation", "session_id", "dialog_id") or "").strip()
        phase = str(row_value(row, "phase", "stage", "step") or "").strip()
        if not conversation_id or not phase:
            raise ValueError(f"line {line_no}: conversation_id and phase are required")

        duration_raw = row_value(row, "duration_ms", "latency_ms", "elapsed_ms")
        duration_sec_raw = row_value(row, "duration_sec", "latency_sec", "elapsed_sec")
        start_raw = row_value(row, "start_ms", "started_at", "start_time")
        end_raw = row_value(row, "end_ms", "ended_at", "end_time")

        if duration_raw is not None or duration_sec_raw is not None:
            duration_ms = float(duration_raw) if duration_raw is not None else float(duration_sec_raw) * 1000
            start_ms = parse_timestamp_ms(start_raw) if start_raw is not None else None
            end_ms = parse_timestamp_ms(end_raw) if end_raw is not None else None
            segments.append(Segment(conversation_id, phase, start_ms, end_ms, duration_ms, line_no))
            continue

        if start_raw is not None and end_raw is not None:
            start_ms = parse_timestamp_ms(start_raw)
            end_ms = parse_timestamp_ms(end_raw)
            segments.append(Segment(conversation_id, phase, start_ms, end_ms, max(0, end_ms - start_ms), line_no))
            continue

        event = str(row_value(row, "event", "type") or "point").strip().lower()
        timestamp_ms = parse_timestamp_ms(row_value(row, "timestamp", "time", "ts"))
        key = (conversation_id, phase)

        if event in {"start", "begin", "phase_start"}:
            open_starts[key] = (timestamp_ms, line_no)
        elif event in {"end", "finish", "done", "phase_end"}:
            if key not in open_starts:
                raise ValueError(f"line {line_no}: end event without matching start for {conversation_id}/{phase}")
            start_ms, start_line = open_starts.pop(key)
            segments.append(Segment(conversation_id, phase, start_ms, timestamp_ms, max(0, timestamp_ms - start_ms), start_line))
        else:
            raise ValueError(
                f"line {line_no}: provide duration_ms, start/end timestamps, or event=start|end with timestamp"
            )

    if open_starts:
        sample_key = next(iter(open_starts))
        raise ValueError(f"unclosed phase start: {sample_key[0]}/{sample_key[1]}")

    return segments


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower)


def ms(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f}s"
    return f"{value:.0f}ms"


def phase_sort_key(phase: str) -> tuple[int, str]:
    try:
        return (DEFAULT_PHASE_ORDER.index(phase), phase)
    except ValueError:
        return (len(DEFAULT_PHASE_ORDER), phase)


def build_summary(segments: list[Segment]) -> dict[str, Any]:
    if not segments:
        raise ValueError("no segments found")

    by_conversation: dict[str, list[Segment]] = defaultdict(list)
    by_phase: dict[str, list[Segment]] = defaultdict(list)
    for segment in segments:
        by_conversation[segment.conversation_id].append(segment)
        by_phase[segment.phase].append(segment)

    conversations = []
    for conversation_id, items in sorted(by_conversation.items()):
        total = sum(item.duration_ms for item in items if item.phase not in OVERLAP_PHASES)
        phases = defaultdict(float)
        for item in items:
            phases[item.phase] += item.duration_ms
        comparable_phases = {
            phase: value for phase, value in phases.items() if phase not in OVERLAP_PHASES
        }
        slowest_phase = max(comparable_phases.items(), key=lambda item: item[1])
        conversations.append(
            {
                "conversation_id": conversation_id,
                "total_ms": total,
                "phase_ms": dict(sorted(phases.items(), key=lambda item: phase_sort_key(item[0]))),
                "slowest_phase": slowest_phase[0],
                "slowest_phase_ms": slowest_phase[1],
            }
        )

    phase_stats = []
    for phase, items in sorted(by_phase.items(), key=lambda item: phase_sort_key(item[0])):
        values = [item.duration_ms for item in items]
        phase_stats.append(
            {
                "phase": phase,
                "count": len(values),
                "total_ms": sum(values),
                "avg_ms": statistics.mean(values),
                "median_ms": statistics.median(values),
                "p95_ms": percentile(values, 0.95),
                "max_ms": max(values),
            }
        )

    totals = [row["total_ms"] for row in conversations]
    first_response_values = [
        item.duration_ms for item in segments if item.phase == "first_response"
    ]
    overall = {
        "conversation_count": len(conversations),
        "segment_count": len(segments),
        "total_ms": sum(totals),
        "avg_conversation_ms": statistics.mean(totals),
        "median_conversation_ms": statistics.median(totals),
        "p95_conversation_ms": percentile(totals, 0.95),
        "max_conversation_ms": max(totals),
        "avg_first_response_ms": statistics.mean(first_response_values)
        if first_response_values
        else None,
        "p95_first_response_ms": percentile(first_response_values, 0.95)
        if first_response_values
        else None,
        "min_first_response_ms": min(first_response_values)
        if first_response_values
        else None,
    }

    conclusions = build_conclusions(overall, phase_stats, conversations)
    return {
        "overall": overall,
        "phase_stats": phase_stats,
        "conversations": conversations,
        "conclusions": conclusions,
    }


def build_conclusions(
    overall: dict[str, Any], phase_stats: list[dict[str, Any]], conversations: list[dict[str, Any]]
) -> list[str]:
    conclusions: list[str] = []
    comparable_phase_stats = [
        item for item in phase_stats if item["phase"] not in OVERLAP_PHASES
    ]
    slow_by_total = max(comparable_phase_stats, key=lambda item: item["total_ms"])
    slow_by_p95 = max(phase_stats, key=lambda item: item["p95_ms"])
    slowest_conversation = max(conversations, key=lambda item: item["total_ms"])

    if overall.get("avg_first_response_ms") is not None:
        conclusions.append(
            f"首响平均 {ms(overall['avg_first_response_ms'])}，P95 {ms(overall['p95_first_response_ms'])}，最快 {ms(overall['min_first_response_ms'])}。"
        )
    conclusions.append(
        f"共分析 {overall['conversation_count']} 次对话、{overall['segment_count']} 个阶段，总平均耗时 {ms(overall['avg_conversation_ms'])}。"
    )
    conclusions.append(
        f"累计耗时最高阶段是 {slow_by_total['phase']}，占总阶段耗时约 {slow_by_total['total_ms'] / overall['total_ms']:.0%}。"
    )
    conclusions.append(f"P95 最慢阶段是 {slow_by_p95['phase']}，P95 为 {ms(slow_by_p95['p95_ms'])}。")
    conclusions.append(
        f"最慢单次对话是 {slowest_conversation['conversation_id']}，总耗时 {ms(slowest_conversation['total_ms'])}，主要瓶颈为 {slowest_conversation['slowest_phase']}。"
    )

    if slow_by_p95["p95_ms"] > slow_by_total["avg_ms"] * 2 and slow_by_p95["count"] >= 3:
        conclusions.append(f"{slow_by_p95['phase']} 存在明显长尾，建议优先检查外部服务、网络抖动或队列等待。")
    if overall["p95_conversation_ms"] > overall["avg_conversation_ms"] * 1.8 and overall["conversation_count"] >= 3:
        conclusions.append("整体对话耗时存在长尾，人工审阅时建议先看最慢 10% 的原始日志。")

    return conclusions


def markdown_report(summary: dict[str, Any]) -> str:
    lines = ["# 对话阶段耗时分析报告", "", "## 自动结论", ""]
    lines.extend(f"- {item}" for item in summary["conclusions"])
    lines.extend(["", "## 总览", ""])
    overall = summary["overall"]
    lines.extend(
        [
            f"- 对话数：{overall['conversation_count']}",
            f"- 阶段数：{overall['segment_count']}",
            f"- 平均单次对话：{ms(overall['avg_conversation_ms'])}",
            f"- P95 单次对话：{ms(overall['p95_conversation_ms'])}",
            f"- 最慢单次对话：{ms(overall['max_conversation_ms'])}",
            *(
                [
                    f"- 平均首响：{ms(overall['avg_first_response_ms'])}",
                    f"- P95 首响：{ms(overall['p95_first_response_ms'])}",
                    f"- 最快首响：{ms(overall['min_first_response_ms'])}",
                ]
                if overall.get("avg_first_response_ms") is not None
                else []
            ),
            "",
            "## 阶段统计",
            "",
            "| 阶段 | 次数 | 平均 | 中位数 | P95 | 最大 | 累计 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in summary["phase_stats"]:
        lines.append(
            f"| {row['phase']} | {row['count']} | {ms(row['avg_ms'])} | {ms(row['median_ms'])} | {ms(row['p95_ms'])} | {ms(row['max_ms'])} | {ms(row['total_ms'])} |"
        )
    lines.extend(["", "## 最慢对话 Top 10", "", "| 对话 | 总耗时 | 最慢阶段 | 最慢阶段耗时 |", "| --- | ---: | --- | ---: |"])
    for row in sorted(summary["conversations"], key=lambda item: item["total_ms"], reverse=True)[:10]:
        lines.append(
            f"| {row['conversation_id']} | {ms(row['total_ms'])} | {row['slowest_phase']} | {ms(row['slowest_phase_ms'])} |"
        )
    return "\n".join(lines) + "\n"


def first_response_metric_html(overall: dict[str, Any]) -> str:
    if overall.get("avg_first_response_ms") is None:
        return ""
    return (
        f'<div class="metric"><span>平均首响</span><strong>{ms(overall["avg_first_response_ms"])}</strong></div>'
        f'<div class="metric"><span>P95 首响</span><strong>{ms(overall["p95_first_response_ms"])}</strong></div>'
        f'<div class="metric"><span>最快首响</span><strong>{ms(overall["min_first_response_ms"])}</strong></div>'
    )


def html_report(summary: dict[str, Any]) -> str:
    phase_stats = summary["phase_stats"]
    conversations = sorted(summary["conversations"], key=lambda item: item["total_ms"], reverse=True)[:30]
    max_phase_total = max(row["total_ms"] for row in phase_stats)
    max_conv_total = max(row["total_ms"] for row in conversations)

    phase_rows = "\n".join(
        f"<tr><td>{html.escape(row['phase'])}</td><td>{row['count']}</td><td>{ms(row['avg_ms'])}</td><td>{ms(row['p95_ms'])}</td><td>{ms(row['total_ms'])}</td></tr>"
        for row in phase_stats
    )
    phase_bars = "\n".join(
        f"<div class='bar-row'><span>{html.escape(row['phase'])}</span><div class='track'><b style='width:{row['total_ms'] / max_phase_total * 100:.1f}%'></b></div><em>{ms(row['total_ms'])}</em></div>"
        for row in phase_stats
    )
    conv_bars = "\n".join(
        f"<div class='bar-row'><span>{html.escape(row['conversation_id'])}</span><div class='track'><b style='width:{row['total_ms'] / max_conv_total * 100:.1f}%'></b></div><em>{ms(row['total_ms'])}</em></div>"
        for row in conversations
    )
    conclusion_items = "\n".join(f"<li>{html.escape(item)}</li>" for item in summary["conclusions"])
    overall = summary["overall"]

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>对话阶段耗时分析</title>
  <style>
    :root {{ color-scheme: light; --ink:#17202a; --muted:#667085; --line:#d8dee8; --accent:#0f766e; --warn:#b45309; --bg:#f7f9fc; }}
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; color:var(--ink); background:var(--bg); }}
    main {{ max-width:1120px; margin:0 auto; padding:32px 20px 48px; }}
    h1 {{ margin:0 0 20px; font-size:32px; letter-spacing:0; }}
    h2 {{ margin:28px 0 12px; font-size:20px; }}
    .metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }}
    .metric, section {{ background:white; border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .metric strong {{ display:block; font-size:24px; margin-top:6px; }}
    .metric span, em {{ color:var(--muted); font-style:normal; }}
    ul {{ margin:0; padding-left:20px; line-height:1.7; }}
    .grid {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
    .bar-row {{ display:grid; grid-template-columns:minmax(90px,150px) 1fr 72px; gap:12px; align-items:center; margin:10px 0; }}
    .bar-row span {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .track {{ height:14px; background:#edf1f7; border-radius:999px; overflow:hidden; }}
    .track b {{ display:block; height:100%; background:linear-gradient(90deg,var(--accent),#22c55e); }}
    table {{ width:100%; border-collapse:collapse; background:white; border:1px solid var(--line); border-radius:8px; overflow:hidden; }}
    th, td {{ text-align:left; padding:10px 12px; border-bottom:1px solid var(--line); }}
    th {{ color:var(--muted); font-weight:600; background:#f1f5f9; }}
    @media (max-width:760px) {{ .grid {{ grid-template-columns:1fr; }} .bar-row {{ grid-template-columns:95px 1fr 64px; }} }}
  </style>
</head>
<body>
<main>
  <h1>对话阶段耗时分析</h1>
    <div class="metrics">
    <div class="metric"><span>对话数</span><strong>{overall['conversation_count']}</strong></div>
    <div class="metric"><span>平均单次对话</span><strong>{ms(overall['avg_conversation_ms'])}</strong></div>
    <div class="metric"><span>P95 单次对话</span><strong>{ms(overall['p95_conversation_ms'])}</strong></div>
    <div class="metric"><span>最慢单次对话</span><strong>{ms(overall['max_conversation_ms'])}</strong></div>
    {first_response_metric_html(overall)}
  </div>
  <h2>自动结论</h2>
  <section><ul>{conclusion_items}</ul></section>
  <div class="grid">
    <section><h2>阶段累计耗时</h2>{phase_bars}</section>
    <section><h2>最慢对话 Top 30</h2>{conv_bars}</section>
  </div>
  <h2>阶段明细</h2>
  <table>
    <thead><tr><th>阶段</th><th>次数</th><th>平均</th><th>P95</th><th>累计</th></tr></thead>
    <tbody>{phase_rows}</tbody>
  </table>
</main>
</body>
</html>
"""


def write_outputs(summary: dict[str, Any], output_prefix: Path, formats: list[str]) -> None:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    for fmt in formats:
        if fmt == "markdown":
            output_prefix.with_suffix(".md").write_text(markdown_report(summary), encoding="utf-8")
        elif fmt == "json":
            output_prefix.with_suffix(".json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        elif fmt == "html":
            output_prefix.with_suffix(".html").write_text(html_report(summary), encoding="utf-8")
        else:
            raise ValueError(f"unsupported format: {fmt}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze staged conversation latency logs.")
    parser.add_argument("input", type=Path, help="Input .jsonl, .json, or .csv file")
    parser.add_argument("-o", "--output-prefix", type=Path, default=Path("reports/dialog-timing"), help="Output path without extension")
    parser.add_argument(
        "--format",
        action="append",
        choices=["markdown", "json", "html"],
        dest="formats",
        help="Output format. Can be passed multiple times. Defaults to markdown+html+json.",
    )
    args = parser.parse_args()

    rows = read_rows(args.input)
    segments = rows_to_segments(rows)
    summary = build_summary(segments)
    write_outputs(summary, args.output_prefix, args.formats or ["markdown", "html", "json"])

    print(markdown_report(summary))
    print(f"Reports written to {args.output_prefix.parent.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
