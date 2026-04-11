from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable
from urllib import error, parse, request


CONFIG_PATH = Path.home() / ".ordersystem_printer_service.json"
TRANSIENT_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_DELAY_SECONDS = 30
RECEIPT_LINE_WIDTH = 42


@dataclass
class AppConfig:
    backend_url: str = "http://127.0.0.1"
    event_id: str = ""
    station_code: str = ""
    access_token: str = ""
    printer_secret: str = ""
    agent_name: str = platform.node() or "printer-agent"
    poll_interval_seconds: int = 2
    printer_mode: str = "preview"
    printer_command: str = "lp {file}"
    output_path: str = str(Path.home() / "ordersystem_tickets.txt")
    escpos_host: str = ""
    escpos_port: int = 9100
    escpos_order_text_size: int = 2
    escpos_table_text_size: int = 3
    escpos_cut_paper: bool = True
    escpos_cut_type: str = "full"


class BackendRequestError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class HealthStatus:
    ok: bool
    message: str


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        return AppConfig()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return AppConfig(**raw)
    except Exception:
        return AppConfig()


def save_config(config: AppConfig) -> None:
    CONFIG_PATH.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def normalize_escpos_cut_type(value: str) -> str:
    return "partial" if value == "partial" else "full"


def check_backend_health(config: AppConfig, timeout: float = 3.0) -> HealthStatus:
    backend_url = config.backend_url.strip().rstrip("/")
    if not backend_url:
        return HealthStatus(False, "Backend URL missing")

    req = request.Request(
        f"{backend_url}/health",
        headers={
            "Accept": "application/json",
            "User-Agent": "OrderSystemPrinterService/1.0",
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status_code = response.getcode()
            if 200 <= status_code < 300:
                return HealthStatus(True, "Connected")
            return HealthStatus(False, f"HTTP {status_code}")
    except error.HTTPError as exc:
        return HealthStatus(False, f"HTTP {exc.code}")
    except error.URLError as exc:
        return HealthStatus(False, f"Unreachable: {exc.reason}")
    except TimeoutError:
        return HealthStatus(False, "Connection timed out")
    except Exception as exc:
        return HealthStatus(False, str(exc))


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists():
        if current == current.parent:
            return None
        current = current.parent
    return current


def check_printer_health(config: AppConfig, timeout: float = 3.0) -> HealthStatus:
    mode = config.printer_mode
    if mode == "preview":
        return HealthStatus(True, "Preview mode")

    if mode == "file":
        output_path = Path(config.output_path).expanduser()
        writable_parent = _nearest_existing_parent(output_path.parent)
        if writable_parent is None:
            return HealthStatus(False, "Output path unavailable")
        if not os.access(writable_parent, os.W_OK):
            return HealthStatus(False, f"No write access: {writable_parent}")
        return HealthStatus(True, "File output ready")

    if mode == "command":
        command = config.printer_command.strip()
        if not command:
            return HealthStatus(False, "Printer command missing")
        try:
            rendered = command.format(
                file="health-check.txt",
                job_id="health-check",
                station_code=config.station_code.strip().lower(),
            )
            parts = shlex.split(rendered)
        except (KeyError, ValueError) as exc:
            return HealthStatus(False, f"Invalid command: {exc}")
        if not parts:
            return HealthStatus(False, "Printer command missing")

        executable = parts[0]
        has_path = os.path.sep in executable or (os.path.altsep is not None and os.path.altsep in executable)
        executable_found = Path(executable).exists() if has_path else shutil.which(executable) is not None
        if not executable_found:
            return HealthStatus(False, f"Command not found: {executable}")
        return HealthStatus(True, "Command available")

    if mode == "escpos-network":
        host = config.escpos_host.strip()
        if not host:
            return HealthStatus(False, "ESC/POS host missing")
        try:
            with socket.create_connection((host, int(config.escpos_port)), timeout=timeout):
                pass
            return HealthStatus(True, f"Connected to {host}:{config.escpos_port}")
        except OSError as exc:
            return HealthStatus(False, f"Printer unreachable: {exc}")

    return HealthStatus(False, f"Unsupported mode: {mode}")


class BackendClient:
    def __init__(self, backend_url: str, access_token: str, printer_secret: str) -> None:
        self.backend_url = backend_url.rstrip("/")
        self.access_token = access_token.strip()
        self.printer_secret = printer_secret.strip()

    def _post(self, path: str, payload: dict) -> object:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.backend_url}{path}",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "OrderSystemPrinterService/1.0",
                "X-Access-Token": self.access_token,
                "X-Printer-Secret": self.printer_secret,
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=20) as response:
                body = response.read().decode("utf-8")
                if not body:
                    return None
                return json.loads(body)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            summary = _summarize_http_error(exc.code, detail, exc.headers.get("Content-Type", ""))
            raise BackendRequestError(
                summary,
                retryable=exc.code in TRANSIENT_HTTP_STATUS_CODES,
                status_code=exc.code,
            ) from exc
        except error.URLError as exc:
            raise BackendRequestError(f"Connection failed: {exc.reason}", retryable=True) from exc
        except TimeoutError as exc:
            raise BackendRequestError("Connection timed out", retryable=True) from exc

    def claim_next_job(self, event_id: str, station_code: str, agent_name: str) -> dict | None:
        payload = {
            "event_id": event_id.strip(),
            "station_code": station_code.strip(),
            "agent_name": agent_name.strip(),
        }
        result = self._post("/print-service/jobs/claim-next", payload)
        return result if isinstance(result, dict) else None

    def complete_job(self, job_id: str, agent_name: str) -> None:
        self._post(f"/print-service/jobs/{parse.quote(job_id)}/complete", {"agent_name": agent_name.strip()})

    def fail_job(self, job_id: str, agent_name: str, error_message: str) -> None:
        self._post(
            f"/print-service/jobs/{parse.quote(job_id)}/fail",
            {"agent_name": agent_name.strip(), "error_message": error_message[:500]},
        )


class TicketPrinter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    @staticmethod
    def _clamp_text_size(value: int) -> int:
        return max(1, min(8, int(value)))

    @staticmethod
    def _escpos_init() -> bytes:
        return b"\x1b@"

    @staticmethod
    def _escpos_align(mode: str) -> bytes:
        mapping = {"left": 0, "center": 1, "right": 2}
        return b"\x1ba" + bytes([mapping.get(mode, 0)])

    @staticmethod
    def _escpos_bold(enabled: bool) -> bytes:
        return b"\x1bE" + (b"\x01" if enabled else b"\x00")

    @staticmethod
    def _escpos_size(size: int) -> bytes:
        normalized = max(1, min(8, size)) - 1
        value = (normalized << 4) | normalized
        return b"\x1d!" + bytes([value])

    @staticmethod
    def _escpos_reset_style() -> bytes:
        return TicketPrinter._escpos_bold(False) + TicketPrinter._escpos_size(1) + TicketPrinter._escpos_align("left")

    @staticmethod
    def _escpos_text(line: str = "") -> bytes:
        return line.encode("cp437", errors="replace") + b"\n"

    @staticmethod
    def _fit_left_text(value: str, width: int) -> str:
        if len(value) <= width:
            return value
        if width <= 3:
            return value[:width]
        return f"{value[:width - 3].rstrip()}..."

    @staticmethod
    def _format_item_line(
        quantity: object,
        name: object,
        unit_price: object,
        total_price: object,
    ) -> str:
        item_text = f"{quantity} x {name}"
        if not unit_price and not total_price:
            return TicketPrinter._fit_left_text(item_text, RECEIPT_LINE_WIDTH)

        price_text = f"{unit_price} / {total_price}"
        max_item_width = RECEIPT_LINE_WIDTH - len(price_text) - 1
        if max_item_width <= 0:
            return TicketPrinter._fit_left_text(price_text, RECEIPT_LINE_WIDTH)

        fitted_item = TicketPrinter._fit_left_text(item_text, max_item_width)
        padding = " " * max(1, RECEIPT_LINE_WIDTH - len(fitted_item) - len(price_text))
        return f"{fitted_item}{padding}{price_text}"

    @staticmethod
    def _feed_lines(n: int) -> bytes:
        n = max(0, min(255, int(n)))
        return b"\x1bd" + bytes([n])   # ESC d n

    @staticmethod
    def _cut_with_feed(dots: int = 0) -> bytes:
        # GS V 66 n
        # Epson Function B:
        # feed paper to cutting position + n * vertical motion unit, then cut
        dots = max(0, min(255, int(dots)))
        return b"\x1d\x56\x42" + bytes([dots])

    def _render_escpos_bytes(self, job: dict, job_id: str) -> bytes:
        payload = job.get("payload_json")
        if not isinstance(payload, dict):
            raise RuntimeError("Missing payload_json for ESC/POS printing")

        station_name = str(
            payload.get("station_name") or payload.get("station_code", self.config.station_code)
        ).upper()
        order_number = payload.get("order_number", "?")
        table_label = payload.get("table_label", "?")
        waiter_short_name = payload.get("waiter_short_name", "?")
        created_at = payload.get("created_at", "")
        job_type = str(payload.get("job_type", "new_order"))
        items = payload.get("items", [])

        heading = "NACHDRUCK" if job_type == "reprint" else "NEUE BESTELLUNG"
        order_size = self._clamp_text_size(self.config.escpos_order_text_size)
        table_size = self._clamp_text_size(self.config.escpos_table_text_size)

        parts: list[bytes] = [self._escpos_init()]
        parts.append(self._escpos_align("center"))
        parts.append(self._escpos_bold(True))
        parts.append(self._escpos_text(station_name))
        parts.append(self._escpos_size(table_size))
        parts.append(self._escpos_text(f"TISCH {table_label}"))
        parts.append(self._escpos_size(order_size))
        parts.append(self._escpos_text(f"Bestellung #{order_number}"))
        parts.append(self._escpos_size(1))
        parts.append(self._feed_lines(1))
        parts.append(self._escpos_text(heading))
        parts.append(self._escpos_bold(False))
        parts.append(self._escpos_text(f"Kellner {waiter_short_name}"))
        parts.append(self._escpos_bold(True))
        parts.append(self._escpos_text(str(created_at)))
        parts.append(self._escpos_bold(False))
        parts.append(self._escpos_reset_style())
        parts.append(self._escpos_text("-" * RECEIPT_LINE_WIDTH))

        for item in items if isinstance(items, list) else []:
            quantity = item.get("quantity", 1)
            name = item.get("menu_item_name", "")
            unit_price = item.get("unit_price")
            total_price = item.get("total_price")
            note = item.get("note")
            parts.append(self._escpos_bold(True))
            parts.append(
                self._escpos_text(
                    self._format_item_line(quantity, name, unit_price, total_price)
                )
            )
            parts.append(self._escpos_bold(False))
            if note:
                wrapped_note = textwrap.fill(
                    str(note),
                    width=RECEIPT_LINE_WIDTH,
                    initial_indent="  Notiz: ",
                    subsequent_indent="  ",
                )
                for note_line in wrapped_note.splitlines():
                    parts.append(self._escpos_text(note_line))

        parts.append(self._escpos_text("-" * RECEIPT_LINE_WIDTH))
        if self.config.escpos_cut_paper:
            # 30 gives about 4.23 mm extra feed on Epson example pages
            # 60 gives about 8.46 mm extra
            parts.append(self._cut_with_feed(60))

        return b"".join(parts)

    def _print_escpos_network(self, job: dict, job_id: str) -> str:
        host = self.config.escpos_host.strip()
        port = int(self.config.escpos_port)
        if not host:
            raise RuntimeError("ESC/POS host is empty")

        payload = self._render_escpos_bytes(job, job_id)
        with socket.create_connection((host, port), timeout=10) as connection:
            connection.sendall(payload)
            connection.shutdown(socket.SHUT_WR)
            time.sleep(0.2)
        return f"Printed via ESC/POS network to {host}:{port}"

    def print_job(self, job: dict, ticket_text: str, job_id: str) -> str:
        if self.config.printer_mode == "escpos-network":
            return self._print_escpos_network(job, job_id)
        return self.print_text(ticket_text, job_id)

    def print_text(self, ticket_text: str, job_id: str) -> str:
        mode = self.config.printer_mode
        if mode == "preview":
            return "Preview mode: ticket captured locally."

        if mode == "file":
            output_path = Path(self.config.output_path).expanduser()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with output_path.open("a", encoding="utf-8") as handle:
                handle.write(ticket_text.rstrip())
                handle.write(f"\n\n=== END {job_id} ===\n\n")
            return f"Written to {output_path}"

        if mode == "command":
            command = self.config.printer_command.strip()
            if not command:
                raise RuntimeError("Printer command is empty")
            with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt") as handle:
                handle.write(ticket_text)
                temp_path = handle.name
            rendered = command.format(
                file=temp_path,
                job_id=job_id,
                station_code=self.config.station_code.strip().lower(),
            )
            completed = subprocess.run(rendered, shell=True, check=False, capture_output=True, text=True)
            if completed.returncode != 0:
                stderr = completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}"
                raise RuntimeError(f"Print command failed: {stderr}")
            return f"Printed via command: {rendered}"

        raise RuntimeError(f"Unsupported printer mode: {mode}")


def validate_required_config(config: AppConfig) -> list[str]:
    missing = [
        name
        for name, value in (
            ("Backend URL", config.backend_url),
            ("Event ID", config.event_id),
            ("Station Code", config.station_code),
            ("Access Token", config.access_token),
            ("Printer Secret", config.printer_secret),
        )
        if not value
    ]
    if config.printer_mode == "escpos-network" and not config.escpos_host.strip():
        missing.append("ESC/POS Host")
    return missing


def _summarize_http_error(status_code: int, detail: str, content_type: str) -> str:
    normalized_type = content_type.lower()
    cleaned = detail.strip()
    if "application/json" in normalized_type or cleaned.startswith("{"):
        try:
            payload = json.loads(cleaned)
            title = str(payload.get("title") or "").strip()
            message = str(payload.get("detail") or payload.get("message") or "").strip()
            if title and message:
                return f"HTTP {status_code}: {title} - {message}"
            if title:
                return f"HTTP {status_code}: {title}"
            if message:
                return f"HTTP {status_code}: {message}"
        except json.JSONDecodeError:
            pass

    if "<html" in cleaned.lower() or "text/html" in normalized_type:
        title_match = re.search(r"<title>(.*?)</title>", cleaned, flags=re.IGNORECASE | re.DOTALL)
        if title_match:
            title = " ".join(title_match.group(1).split())
            return f"HTTP {status_code}: {title}"
        stripped = re.sub(r"<[^>]+>", " ", cleaned)
        compact = " ".join(stripped.split())
        if compact:
            return f"HTTP {status_code}: {compact[:160]}"
        return f"HTTP {status_code}: HTML error response"

    if cleaned:
        single_line = " ".join(cleaned.split())
        return f"HTTP {status_code}: {single_line[:160]}"
    return f"HTTP {status_code}"


def _sleep_with_stop(seconds: float, stop_requested: Callable[[], bool]) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while not stop_requested() and time.monotonic() < deadline:
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


def _next_retry_delay(attempt: int, base_delay: int) -> int:
    normalized_base = max(1, int(base_delay))
    return min(MAX_RETRY_DELAY_SECONDS, normalized_base * (2 ** max(0, attempt)))


def _report_job_state_with_retry(
    *,
    action: Callable[[], None],
    action_label: str,
    job_id: str,
    stop_requested: Callable[[], bool],
    log_callback: Callable[[str], None],
    base_delay: int,
) -> bool:
    attempt = 0
    while not stop_requested():
        try:
            action()
            if attempt > 0:
                log_callback(f"Backend connection restored. {action_label} confirmed for {job_id}.")
            return True
        except BackendRequestError as exc:
            if not exc.retryable:
                log_callback(f"{action_label} failed for {job_id}: {exc}")
                return False
            delay = _next_retry_delay(attempt, base_delay)
            log_callback(f"{action_label} pending for {job_id}: {exc}. Retrying in {delay}s.")
            attempt += 1
            _sleep_with_stop(delay, stop_requested)
        except Exception as exc:
            log_callback(f"{action_label} failed for {job_id}: {exc}")
            return False
    return False


def run_worker_loop(
    config: AppConfig,
    stop_requested: Callable[[], bool],
    log_callback: Callable[[str], None],
    preview_callback: Callable[[str], None] | None = None,
) -> None:
    client = BackendClient(config.backend_url, config.access_token, config.printer_secret)
    printer = TicketPrinter(config)
    backend_retry_attempt = 0
    backend_unavailable = False

    while not stop_requested():
        job_id: str | None = None
        try:
            job = client.claim_next_job(config.event_id, config.station_code, config.agent_name)
            if backend_unavailable:
                log_callback("Backend connection restored.")
                backend_unavailable = False
                backend_retry_attempt = 0
            if job is None:
                _sleep_with_stop(config.poll_interval_seconds, stop_requested)
                continue

            job_id = str(job.get("id", "unknown-job"))
            rendered_text = str(job.get("rendered_text", "")).strip() + "\n"
            if preview_callback is not None:
                preview_callback(rendered_text)
            log_callback(f"Claimed job {job_id}")
            result = printer.print_job(job, rendered_text, job_id)
            completed = _report_job_state_with_retry(
                action=lambda: client.complete_job(job_id, config.agent_name),
                action_label="Completion",
                job_id=job_id,
                stop_requested=stop_requested,
                log_callback=log_callback,
                base_delay=config.poll_interval_seconds,
            )
            if completed:
                log_callback(f"{result} | completed {job_id}")
            elif stop_requested():
                log_callback(f"Stopped before completion could be confirmed for {job_id}.")
            else:
                log_callback(f"Printed {job_id}, but completion could not be confirmed.")
        except BackendRequestError as exc:
            delay = _next_retry_delay(backend_retry_attempt, config.poll_interval_seconds)
            if not backend_unavailable:
                log_callback(f"Backend unavailable: {exc}. Retrying in {delay}s.")
            else:
                log_callback(f"Backend still unavailable: {exc}. Retrying in {delay}s.")
            backend_unavailable = True
            backend_retry_attempt += 1
            _sleep_with_stop(delay, stop_requested)
        except Exception as exc:
            log_callback(f"Worker error: {exc}")
            if job_id is not None:
                failed = _report_job_state_with_retry(
                    action=lambda: client.fail_job(job_id, config.agent_name, str(exc)),
                    action_label="Failure report",
                    job_id=job_id,
                    stop_requested=stop_requested,
                    log_callback=log_callback,
                    base_delay=config.poll_interval_seconds,
                )
                if not failed and not stop_requested():
                    log_callback(f"Local print error for {job_id} could not be reported to the backend.")
            _sleep_with_stop(config.poll_interval_seconds, stop_requested)
