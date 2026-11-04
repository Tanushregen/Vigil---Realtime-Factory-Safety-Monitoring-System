"""Per-camera quality numbers: precision, recall, false alerts, latency, cost.

Precision and false alerts come from people marking alerts true / false.
Recall comes from people reporting events VIGIL missed. Latency, tokens and
the share of scans motion gating avoided are counted by the detector, in
hourly buckets, so the table stays small.

Every number is None when there is nothing to compute it from - never a
made-up zero.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.db import Camera, MissedEvent, ScanStat, SessionLocal, Violation
from app.detector import config

logger = logging.getLogger(__name__)


def _hour_start(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def record_scan(camera_id: int, ok: bool, latency_s: float = 0.0,
                usage: dict | None = None, skipped: bool = False) -> None:
    """Count one model scan (or one scan motion gating avoided). Never raises."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    hour = _hour_start(now)
    usage = usage or {}
    db = SessionLocal()
    try:
        row = (db.query(ScanStat)
               .filter(ScanStat.camera_id == camera_id, ScanStat.hour == hour).first())
        if row is None:
            row = ScanStat(camera_id=camera_id, hour=hour, scans=0, failures=0, skipped=0,
                           latency_ms_sum=0, latency_ms_max=0,
                           prompt_tokens=0, completion_tokens=0)
            db.add(row)

        if skipped:
            row.skipped += 1
        else:
            ms = int(max(0.0, latency_s) * 1000)
            row.scans += 1
            row.latency_ms_sum += ms
            row.latency_ms_max = max(row.latency_ms_max, ms)
            if not ok:
                row.failures += 1
            row.prompt_tokens += int(usage.get("prompt_tokens") or 0)
            row.completion_tokens += int(usage.get("completion_tokens") or 0)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("[metrics] could not count a scan", exc_info=True)
    finally:
        db.close()


def record_check(camera_id: int, rejected: bool) -> None:
    """Count one borderline alert re-judged on a clip. Never raises."""
    hour = _hour_start(datetime.now(timezone.utc).replace(tzinfo=None))
    db = SessionLocal()
    try:
        row = (db.query(ScanStat)
               .filter(ScanStat.camera_id == camera_id, ScanStat.hour == hour).first())
        if row is None:
            row = ScanStat(camera_id=camera_id, hour=hour, scans=0, failures=0, skipped=0,
                           latency_ms_sum=0, latency_ms_max=0, prompt_tokens=0,
                           completion_tokens=0, checked=0, rejected=0)
            db.add(row)
        row.checked += 1
        if rejected:
            row.rejected += 1
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("[metrics] could not count a clip check", exc_info=True)
    finally:
        db.close()


def _percentile(values: list[int], q: float):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def _ratio(numerator: int, denominator: int):
    return round(numerator / denominator, 4) if denominator else None


def cost_usd(prompt_tokens: int, completion_tokens: int):
    """None when the prices are not set (META_PRICE_IN_PER_M / META_PRICE_OUT_PER_M)."""
    if not (config.META_PRICE_IN or config.META_PRICE_OUT):
        return None
    return round(prompt_tokens / 1e6 * config.META_PRICE_IN
                 + completion_tokens / 1e6 * config.META_PRICE_OUT, 4)


def summary(since_hours: int = 24, camera_id: int | None = None) -> dict:
    """Numbers for every camera over the last `since_hours` hours."""
    since_hours = max(1, since_hours)
    local_since = datetime.now() - timedelta(hours=since_hours)
    utc_since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=since_hours)
    days = since_hours / 24

    db = SessionLocal()
    try:
        cameras = {c.id: c.name for c in db.query(Camera).all()}

        violations = db.query(Violation).filter(Violation.occurred_at >= local_since)
        missed = db.query(MissedEvent).filter(MissedEvent.occurred_at >= local_since)
        stats = db.query(ScanStat).filter(ScanStat.hour >= _hour_start(utc_since))
        if camera_id is not None:
            violations = violations.filter(Violation.camera_id == camera_id)
            missed = missed.filter(MissedEvent.camera_id == camera_id)
            stats = stats.filter(ScanStat.camera_id == camera_id)

        per: dict[int, dict] = {}

        def entry(cid: int) -> dict:
            return per.setdefault(cid, {
                "camera_id": cid, "camera_name": cameras.get(cid),
                "alerts": 0, "true_alerts": 0, "false_alerts": 0, "unreviewed": 0,
                "missed": 0, "latencies": [],
                "scans": 0, "failures": 0, "skipped": 0,
                "scan_ms_sum": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "checked": 0, "rejected": 0,
            })

        for v in violations.all():
            e = entry(v.camera_id)
            e["alerts"] += 1
            if v.review == "true":
                e["true_alerts"] += 1
            elif v.review == "false":
                e["false_alerts"] += 1
            else:
                e["unreviewed"] += 1
            if v.latency_ms is not None:
                e["latencies"].append(v.latency_ms)

        for m in missed.all():
            entry(m.camera_id)["missed"] += 1

        for s in stats.all():
            e = entry(s.camera_id)
            e["scans"] += s.scans
            e["failures"] += s.failures
            e["skipped"] += s.skipped
            e["scan_ms_sum"] += s.latency_ms_sum
            e["prompt_tokens"] += s.prompt_tokens
            e["completion_tokens"] += s.completion_tokens
            e["checked"] += s.checked or 0
            e["rejected"] += s.rejected or 0
    finally:
        db.close()

    cameras_out = []
    for cid in sorted(per):
        e = per[cid]
        reviewed = e["true_alerts"] + e["false_alerts"]
        cameras_out.append({
            "camera_id": cid,
            "camera_name": e["camera_name"],
            "alerts": e["alerts"],
            "reviewed": reviewed,
            "unreviewed": e["unreviewed"],
            "true_alerts": e["true_alerts"],
            "false_alerts": e["false_alerts"],
            "missed": e["missed"],
            "precision": _ratio(e["true_alerts"], reviewed),
            "recall": _ratio(e["true_alerts"], e["true_alerts"] + e["missed"]),
            "false_alerts_per_day": round(e["false_alerts"] / days, 2) if reviewed else None,
            "alert_latency_p50_ms": _percentile(e["latencies"], 0.5),
            "alert_latency_p95_ms": _percentile(e["latencies"], 0.95),
            "scans": e["scans"],
            "scan_failures": e["failures"],
            "scan_latency_avg_ms": round(e["scan_ms_sum"] / e["scans"]) if e["scans"] else None,
            "idle_scans_avoided": e["skipped"],
            "motion_gating_saved": _ratio(e["skipped"], e["skipped"] + e["scans"]),
            "borderline_checked": e["checked"],
            "borderline_rejected": e["rejected"],
            "prompt_tokens": e["prompt_tokens"],
            "completion_tokens": e["completion_tokens"],
            "cost_usd": cost_usd(e["prompt_tokens"], e["completion_tokens"]),
            "cost_per_day_usd": (round(c / days, 4) if (c := cost_usd(
                e["prompt_tokens"], e["completion_tokens"])) is not None else None),
        })

    def total(key):
        return sum(c[key] for c in cameras_out)

    t_true, t_false, t_missed = total("true_alerts"), total("false_alerts"), total("missed")
    t_prompt, t_completion = total("prompt_tokens"), total("completion_tokens")
    return {
        "since_hours": since_hours,
        "cost_prices_set": bool(config.META_PRICE_IN or config.META_PRICE_OUT),
        "totals": {
            "alerts": total("alerts"),
            "reviewed": t_true + t_false,
            "precision": _ratio(t_true, t_true + t_false),
            "recall": _ratio(t_true, t_true + t_missed),
            "false_alerts": t_false,
            "missed": t_missed,
            "scans": total("scans"),
            "idle_scans_avoided": total("idle_scans_avoided"),
            "borderline_checked": total("borderline_checked"),
            "borderline_rejected": total("borderline_rejected"),
            "prompt_tokens": t_prompt,
            "completion_tokens": t_completion,
            "cost_usd": cost_usd(t_prompt, t_completion),
        },
        "cameras": cameras_out,
    }
