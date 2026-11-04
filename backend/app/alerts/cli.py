"""Command line:  python -m app.alerts.cli  preview|send|b2-poll|retry|status  ...

    preview event.json [more.json | dir/ ...]   parse + format + validate clip(s); prints messages, sends NOTHING
    send event.json [more.json | dir/ ...]      full workflow for one or more JSON files / folders
    b2-poll [--send] [--watch SECONDS]          poll B2 for alert JSON; preview by default
    b2-process-json event.json [--send]         process local JSON with its B2 video; preview by default
    retry [--watch SECONDS]                     re-attempt FAILED alerts;  retry --event EVT001 --force  for a DEAD one
    status                                      outbox counts, failed alerts and rejected payloads
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from .config import _clean_env


def _setup_logging() -> None:
    reconfigure_stdout = getattr(sys.stdout, "reconfigure", None)
    if reconfigure_stdout is not None:
        reconfigure_stdout(errors="replace")
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = _clean_env("VIGIL_LOG_FILE")
    if log_file:
        handlers.append(logging.handlers.RotatingFileHandler(log_file, maxBytes=5_000_000,
                                                             backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=_clean_env("VIGIL_LOG_LEVEL", "INFO"), handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)      # httpx logs request URLs; keep them out of the log


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass                      # python-dotenv is optional; real environment variables still work


def _print_result(r) -> int:
    src = f"  file={Path(r.source).name}" if getattr(r, "source", None) else ""
    print(f"{r.status}  event={r.event_id}{src}  ids={r.message_ids or '-'}" + (f"  [{r.error_kind}] {r.error}" if r.error else ""))
    return 0 if r.ok else 1


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if seconds <= 0:
        raise argparse.ArgumentTypeError("seconds must be greater than zero")
    return seconds


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="app.alerts.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("preview", "send"):
        p_cmd = sub.add_parser(name)
        p_cmd.add_argument("json_files", nargs="+", help="one or more JSON files or directories containing JSON files")
        p_cmd.add_argument("--no-recursive", action="store_true", help="do not recurse into subdirectories")
    rp = sub.add_parser("retry")
    rp.add_argument("--event")
    rp.add_argument("--force", action="store_true", help="retry even if the event ran out of attempts")
    rp.add_argument("--watch", type=float, metavar="SECONDS", help="keep retrying every SECONDS")
    sub.add_parser("status")
    bp = sub.add_parser("b2-poll", help="poll Backblaze B2 for new JSON events; preview only unless --send is set")
    bp.add_argument("--send", action="store_true", help="send alerts through WhatsApp (live)")
    bp.add_argument("--watch", type=_positive_seconds, metavar="SECONDS",
                    help="poll repeatedly at this interval")
    bp.add_argument("--prefix", help="override B2_JSON_PREFIX for this run")
    bj = sub.add_parser("b2-process-json", help="use a local event JSON to fetch its referenced B2 video")
    bj.add_argument("json_file", help="local event JSON whose clip_path is the exact B2 video object key")
    bj.add_argument("--send", action="store_true", help="send the alert through WhatsApp (live)")
    args = ap.parse_args(argv)

    _load_env()
    _setup_logging()
    if args.cmd in ("b2-poll", "b2-process-json"):
        from dataclasses import replace

        from .alert_service import AlertService
        from .b2_poller import B2Poller, print_outcome
        from .config import AlertSettings, B2Settings, ConfigError

        try:
            b2_settings = B2Settings.from_env()
            if args.cmd == "b2-poll" and args.prefix is not None:
                b2_settings = replace(b2_settings, json_prefix=args.prefix)
            settings = AlertSettings.from_env()
            poller = B2Poller(b2_settings.create_s3_client(), b2_settings, settings.clips_root)
            service = (AlertService.from_env(min_clip_age_s=0.0) if args.send
                       else _offline_service(min_clip_age_s=0.0))
        except ConfigError as e:
            print(f"NOT OK: {e}")
            return 1

        if args.cmd == "b2-process-json":
            outcome = poller.process_local_json(args.json_file, service, send=args.send)
            print_outcome(outcome)
            return 0 if outcome.status in ("PREVIEW", "SENT", "DUPLICATE") else 1

        overall_ok = True
        while True:
            try:
                outcomes = poller.poll_once(service, send=args.send)
            except RuntimeError as e:
                print(f"NOT OK: {e}")
                return 1
            if not outcomes:
                print("No alert JSON objects found in the configured B2 prefix.")
            for outcome in outcomes:
                print_outcome(outcome)
                if outcome.status not in ("PREVIEW", "SENT", "DUPLICATE"):
                    overall_ok = False
            if not args.watch:
                return 0 if overall_ok else 1
            time.sleep(args.watch)

    from .alert_service import AlertService, discover_json_files
    from .clip_validator import ClipNotReady, ClipRejected
    from .json_parser import AlertInputError

    svc = AlertService.from_env() if args.cmd != "preview" else _offline_service()
    if args.cmd == "preview":
        targets = discover_json_files(args.json_files, recursive=not args.no_recursive)
        if not targets:
            print("NOT OK: no JSON files found")
            return 1
        rc = 0
        for idx, target in enumerate(targets):
            if idx > 0:
                print("\n" + "=" * 60 + "\n")
            try:
                p = svc.preview(target)
            except (AlertInputError, ClipRejected, ClipNotReady) as e:
                print(f"NOT OK ({target}): {e}")
                rc = 1
                continue
            print(p.text, "\n\n--- same alert via approved template ---\n\n", p.template_text,
                  f"\n\nclip: {p.clip.clip_path} ({p.clip.size_bytes} bytes, sha256 {p.clip.sha256[:12]}...)", sep="")
        return rc
    if args.cmd == "send":
        results = svc.process_many(args.json_files, recursive=not args.no_recursive)
        if not results:
            print("NOT OK: no JSON files found")
            return 1
        rc = 0
        for r in results:
            if _print_result(r) != 0:
                rc = 1
        return rc
    if args.cmd == "retry":
        if args.event:
            return _print_result(svc.retry_event(args.event, force=args.force))
        while True:
            results = svc.retry_failed()
            for r in results:
                _print_result(r)
            if not args.watch:
                return 0 if all(r.ok for r in results) else 1
            time.sleep(args.watch)
    if args.cmd == "status":
        print("outbox:", svc.outbox.counts() or "empty")
        for r in svc.outbox.list_by_status("FAILED", "DEAD"):
            print(f"  {r.status:6} {r.event_id}  attempts={r.attempts}  [{r.error_kind}] {r.last_error}")
        for r in svc.outbox.list_rejected(10):
            print(f"  REJECTED {r['received_at']}: {r['error']}")
        return 0
    return 2


def _offline_service(*, min_clip_age_s=None):
    """preview must work without WhatsApp credentials."""
    from .alert_outbox import AlertOutbox
    from .alert_service import AlertService
    from .clip_validator import AlertClipValidator
    from .config import AlertSettings
    st = AlertSettings.from_env()
    return AlertService(None, AlertOutbox(":memory:"),   # type: ignore[arg-type]
                        AlertClipValidator(st.clips_root, st.clip_base_dir, enforce_name_match=st.enforce_clip_name_match,
                                           check_codecs=st.check_codecs,
                                           min_age_s=st.clip_min_age_s if min_clip_age_s is None else min_clip_age_s), st)


if __name__ == "__main__":
    sys.exit(main())
