from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

from core import (
    AppConfig,
    TicketPrinter,
    load_config,
    resolve_config_path,
    run_worker_loop,
    save_config,
    validate_required_config,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OrderSystem Printer Service (headless)")
    parser.add_argument("--instance-name", help="Use a separate named config file for this instance")
    parser.add_argument("--config-path", help="Use an explicit config file path for this instance")
    parser.add_argument("--backend-url")
    parser.add_argument("--event-id")
    parser.add_argument("--station-code")
    parser.add_argument("--oidc-token-url")
    parser.add_argument("--oidc-client-id")
    parser.add_argument("--oidc-client-secret")
    parser.add_argument("--agent-name")
    parser.add_argument("--poll-interval-seconds", type=int)
    parser.add_argument("--printer-mode", choices=("preview", "file", "command", "escpos-network"))
    parser.add_argument("--printer-command")
    parser.add_argument("--output-path")
    parser.add_argument("--escpos-host")
    parser.add_argument("--escpos-port", type=int)
    parser.add_argument("--escpos-order-text-size", type=int)
    parser.add_argument("--escpos-table-text-size", type=int)
    parser.add_argument("--save-config", action="store_true", help="Persist the effective configuration before starting")
    parser.add_argument("--test-print", action="store_true", help="Run one local test print and exit")
    return parser


def merge_config(base: AppConfig, args: argparse.Namespace) -> AppConfig:
    config = AppConfig(
        backend_url=args.backend_url or base.backend_url,
        event_id=args.event_id or base.event_id,
        station_code=(args.station_code or base.station_code).lower(),
        oidc_token_url=args.oidc_token_url or base.oidc_token_url,
        oidc_client_id=args.oidc_client_id or base.oidc_client_id,
        oidc_client_secret=args.oidc_client_secret or base.oidc_client_secret,
        agent_name=args.agent_name or base.agent_name,
        poll_interval_seconds=args.poll_interval_seconds or base.poll_interval_seconds,
        printer_mode=args.printer_mode or base.printer_mode,
        printer_command=args.printer_command or base.printer_command,
        output_path=args.output_path or base.output_path,
        escpos_host=args.escpos_host or base.escpos_host,
        escpos_port=args.escpos_port or base.escpos_port,
        escpos_order_text_size=args.escpos_order_text_size or base.escpos_order_text_size,
        escpos_table_text_size=args.escpos_table_text_size or base.escpos_table_text_size,
    )
    return config


def _effective_agent_name(config: AppConfig, instance_name: str | None) -> str:
    instance = (instance_name or "").strip()
    if not instance:
        return config.agent_name

    default_agent_name = AppConfig().agent_name
    if config.agent_name == default_agent_name:
        return f"{config.agent_name}-{instance}"
    return config.agent_name


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
    resolved_config_path = resolve_config_path(instance_name=args.instance_name, config_path=args.config_path)
    config = merge_config(load_config(resolved_config_path), args)
    config.agent_name = _effective_agent_name(config, args.instance_name)

    if args.save_config:
        save_config(config, resolved_config_path)
        log(f"Configuration saved to {Path(resolved_config_path)}.")

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

    log(f"Using config file {Path(resolved_config_path)}")
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
