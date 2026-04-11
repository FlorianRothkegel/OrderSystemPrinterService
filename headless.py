from __future__ import annotations

import argparse
import signal
import sys
import time

from core import (
    AppConfig,
    TicketPrinter,
    load_config,
    normalize_escpos_cut_type,
    run_worker_loop,
    save_config,
    validate_required_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OrderSystem Printer Service (headless)")
    parser.add_argument("--backend-url")
    parser.add_argument("--event-id")
    parser.add_argument("--station-code")
    parser.add_argument("--access-token")
    parser.add_argument("--printer-secret")
    parser.add_argument("--agent-name")
    parser.add_argument("--poll-interval-seconds", type=int)
    parser.add_argument("--printer-mode", choices=("preview", "file", "command", "escpos-network"))
    parser.add_argument("--printer-command")
    parser.add_argument("--output-path")
    parser.add_argument("--escpos-host")
    parser.add_argument("--escpos-port", type=int)
    parser.add_argument("--escpos-order-text-size", type=int)
    parser.add_argument("--escpos-table-text-size", type=int)
    parser.add_argument("--escpos-cut-type", choices=("full", "partial"))
    parser.add_argument("--save-config", action="store_true", help="Persist the effective configuration before starting")
    parser.add_argument("--test-print", action="store_true", help="Run one local test print and exit")
    return parser


def merge_config(base: AppConfig, args: argparse.Namespace) -> AppConfig:
    config = AppConfig(
        backend_url=args.backend_url or base.backend_url,
        event_id=args.event_id or base.event_id,
        station_code=(args.station_code or base.station_code).lower(),
        access_token=args.access_token or base.access_token,
        printer_secret=args.printer_secret or base.printer_secret,
        agent_name=args.agent_name or base.agent_name,
        poll_interval_seconds=args.poll_interval_seconds or base.poll_interval_seconds,
        printer_mode=args.printer_mode or base.printer_mode,
        printer_command=args.printer_command or base.printer_command,
        output_path=args.output_path or base.output_path,
        escpos_host=args.escpos_host or base.escpos_host,
        escpos_port=args.escpos_port or base.escpos_port,
        escpos_order_text_size=args.escpos_order_text_size or base.escpos_order_text_size,
        escpos_table_text_size=args.escpos_table_text_size or base.escpos_table_text_size,
        escpos_cut_type=normalize_escpos_cut_type(args.escpos_cut_type or base.escpos_cut_type),
    )
    return config


def log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def run_test_print(config: AppConfig) -> int:
    sample = (
        f"{config.station_code.upper() or 'STATION'}\n"
        "TESTDRUCK\n\n"
        "Bestellung #999\n"
        "Tisch Test\n"
        "Kellner QA\n"
        "1 x Testgericht\n"
        "  Notiz: Druckerservice pruefen\n"
    )
    try:
        result = TicketPrinter(config).print_text(sample, "test-job")
        log(result)
        if config.printer_mode == "preview":
            print(sample, flush=True)
        return 0
    except Exception as exc:
        log(f"Test print failed: {exc}")
        return 1


def main() -> int:
    args = build_parser().parse_args()
    config = merge_config(load_config(), args)

    if args.save_config:
        save_config(config)
        log("Configuration saved.")

    if args.test_print:
        return run_test_print(config)

    missing = validate_required_config(config)
    if missing:
        log(f"Missing configuration: {', '.join(missing)}")
        return 2

    stop = {"requested": False}

    def request_stop(signum: int, frame: object) -> None:
        del signum, frame
        stop["requested"] = True
        log("Stop requested.")

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    log(f"Starting headless printer service for station '{config.station_code}'")
    run_worker_loop(
        config,
        stop_requested=lambda: stop["requested"],
        log_callback=log,
        preview_callback=lambda text: print(text, flush=True) if config.printer_mode == "preview" else None,
    )
    log("Printer service stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
