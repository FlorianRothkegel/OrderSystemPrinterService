from __future__ import annotations

import queue
import threading
import time
from tkinter import END, Label, StringVar, TclError, Text, Tk, ttk, messagebox

from core import (
    AppConfig,
    HealthStatus,
    TicketPrinter,
    check_backend_health,
    check_printer_health,
    load_config,
    normalize_escpos_cut_type,
    run_worker_loop,
    save_config,
    validate_required_config,
)


STATUS_OK = "#1f8f46"
STATUS_BAD = "#b3261e"
STATUS_WAITING = "#8a6d1d"
STATUS_IDLE = "#666666"


class PrinterServiceApp:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title("OrderSystem Printer Service")
        self.root.geometry("920x760")

        self.log_queue: queue.Queue[str] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.health_check_running = False
        self.health_after_id: str | None = None
        self.closed = False

        loaded = load_config()
        self.backend_url = StringVar(value=loaded.backend_url)
        self.event_id = StringVar(value=loaded.event_id)
        self.station_code = StringVar(value=loaded.station_code)
        self.access_token = StringVar(value=loaded.access_token)
        self.printer_secret = StringVar(value=loaded.printer_secret)
        self.agent_name = StringVar(value=loaded.agent_name)
        self.poll_interval_seconds = StringVar(value=str(loaded.poll_interval_seconds))
        self.printer_mode = StringVar(value=loaded.printer_mode)
        self.printer_command = StringVar(value=loaded.printer_command)
        self.output_path = StringVar(value=loaded.output_path)
        self.escpos_host = StringVar(value=loaded.escpos_host)
        self.escpos_port = StringVar(value=str(loaded.escpos_port))
        self.escpos_order_text_size = StringVar(value=str(loaded.escpos_order_text_size))
        self.escpos_table_text_size = StringVar(value=str(loaded.escpos_table_text_size))
        self.escpos_cut_type = StringVar(value=normalize_escpos_cut_type(loaded.escpos_cut_type))
        self.status_text = StringVar(value="Stopped")
        self.backend_status_text = StringVar(value="Backend: unchecked")
        self.printer_status_text = StringVar(value="Printer: unchecked")

        self._build_ui()
        self.printer_mode.trace_add("write", lambda *_: self._refresh_mode_fields())
        self._refresh_mode_fields()
        self._set_running_ui(False)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(200, self._drain_logs)
        self._schedule_health_check(500)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=3)
        frame.rowconfigure(6, weight=2)

        status_row = ttk.Frame(frame)
        status_row.grid(row=0, column=0, sticky="ew")
        status_row.columnconfigure(0, weight=1)
        status_row.columnconfigure(1, weight=1)
        status_row.columnconfigure(2, weight=1)

        self.backend_status_badge = Label(
            status_row,
            textvariable=self.backend_status_text,
            bg=STATUS_IDLE,
            fg="white",
            padx=12,
            pady=6,
            anchor="w",
        )
        self.backend_status_badge.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.printer_status_badge = Label(
            status_row,
            textvariable=self.printer_status_text,
            bg=STATUS_IDLE,
            fg="white",
            padx=12,
            pady=6,
            anchor="w",
        )
        self.printer_status_badge.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        ttk.Label(status_row, textvariable=self.status_text).grid(row=0, column=2, sticky="e")

        self.settings_frame = ttk.LabelFrame(frame, text="Settings", padding=12)
        self.settings_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.settings_frame.columnconfigure(1, weight=1)

        common_fields = [
            ("Backend URL", self.backend_url, ""),
            ("Event ID", self.event_id, ""),
            ("Station Code", self.station_code, ""),
            ("Access Token", self.access_token, "*"),
            ("Printer Secret", self.printer_secret, "*"),
            ("Agent Name", self.agent_name, ""),
            ("Poll Interval (s)", self.poll_interval_seconds, ""),
        ]
        for row_index, (label, variable, show) in enumerate(common_fields):
            self._add_labeled_entry(self.settings_frame, row_index, label, variable, show=show)

        mode_row = len(common_fields)
        ttk.Label(self.settings_frame, text="Printer Mode").grid(
            row=mode_row,
            column=0,
            sticky="w",
            padx=(0, 12),
            pady=6,
        )
        ttk.Combobox(
            self.settings_frame,
            textvariable=self.printer_mode,
            state="readonly",
            values=("preview", "file", "command", "escpos-network"),
        ).grid(row=mode_row, column=1, sticky="ew", pady=6)

        self.mode_container = ttk.Frame(self.settings_frame)
        self.mode_container.grid(row=mode_row + 1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.mode_container.columnconfigure(0, weight=1)

        self.mode_frames: dict[str, ttk.Frame] = {}
        self._build_file_settings()
        self._build_command_settings()
        self._build_escpos_settings()

        self.button_row = ttk.Frame(frame)
        self.button_row.grid(row=2, column=0, sticky="ew", pady=(12, 8))
        for index in range(5):
            self.button_row.columnconfigure(index, weight=1)

        self.save_button = ttk.Button(self.button_row, text="Save Config", command=self._save_clicked)
        self.start_button = ttk.Button(self.button_row, text="Start", command=self._start_clicked)
        self.stop_button = ttk.Button(self.button_row, text="Stop", command=self._stop_clicked)
        self.test_button = ttk.Button(self.button_row, text="Test Print", command=self._test_print_clicked)
        self.health_button = ttk.Button(self.button_row, text="Check Status", command=self._check_status_clicked)

        ttk.Label(frame, text="Activity Log").grid(row=3, column=0, sticky="w")
        self.log_text = Text(frame, height=14, wrap="word")
        self.log_text.grid(row=4, column=0, sticky="nsew")

        ttk.Label(frame, text="Last Ticket Preview").grid(row=5, column=0, sticky="w", pady=(12, 0))
        self.preview_text = Text(frame, height=12, wrap="word")
        self.preview_text.grid(row=6, column=0, sticky="nsew")

    def _add_labeled_entry(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: StringVar,
        *,
        show: str = "",
    ) -> ttk.Entry:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
        entry = ttk.Entry(parent, textvariable=variable, show=show)
        entry.grid(row=row, column=1, sticky="ew", pady=6)
        return entry

    def _build_file_settings(self) -> None:
        frame = ttk.Frame(self.mode_container)
        frame.columnconfigure(1, weight=1)
        self._add_labeled_entry(frame, 0, "Output File", self.output_path)
        frame.grid(row=0, column=0, sticky="ew")
        self.mode_frames["file"] = frame

    def _build_command_settings(self) -> None:
        frame = ttk.Frame(self.mode_container)
        frame.columnconfigure(1, weight=1)
        self._add_labeled_entry(frame, 0, "Printer Command", self.printer_command)
        frame.grid(row=0, column=0, sticky="ew")
        self.mode_frames["command"] = frame

    def _build_escpos_settings(self) -> None:
        frame = ttk.Frame(self.mode_container)
        frame.columnconfigure(1, weight=1)
        self._add_labeled_entry(frame, 0, "ESC/POS Host", self.escpos_host)
        self._add_labeled_entry(frame, 1, "ESC/POS Port", self.escpos_port)
        self._add_labeled_entry(frame, 2, "Order Text Size", self.escpos_order_text_size)
        self._add_labeled_entry(frame, 3, "Table Text Size", self.escpos_table_text_size)
        ttk.Label(frame, text="Cut Type").grid(row=4, column=0, sticky="w", padx=(0, 12), pady=6)
        ttk.Combobox(
            frame,
            textvariable=self.escpos_cut_type,
            state="readonly",
            values=("full", "partial"),
        ).grid(row=4, column=1, sticky="ew", pady=6)
        frame.grid(row=0, column=0, sticky="ew")
        self.mode_frames["escpos-network"] = frame

    def _refresh_mode_fields(self) -> None:
        selected_mode = self.printer_mode.get().strip()
        for mode_frame in self.mode_frames.values():
            mode_frame.grid_remove()
        if selected_mode in self.mode_frames:
            self.mode_frames[selected_mode].grid()

    def _set_running_ui(self, running: bool) -> None:
        if running:
            self.settings_frame.grid_remove()
            for button in (self.save_button, self.start_button, self.test_button, self.health_button):
                button.grid_remove()
            self.stop_button.grid(row=0, column=0, columnspan=5, sticky="ew", padx=4)
            self.stop_button.configure(state="normal")
            return

        self.settings_frame.grid()
        self.save_button.grid(row=0, column=0, sticky="ew", padx=4)
        self.start_button.grid(row=0, column=1, sticky="ew", padx=4)
        self.stop_button.grid(row=0, column=2, sticky="ew", padx=4)
        self.test_button.grid(row=0, column=3, sticky="ew", padx=4)
        self.health_button.grid(row=0, column=4, sticky="ew", padx=4)
        self.stop_button.configure(state="disabled")
        self._refresh_mode_fields()

    def _current_config(self) -> AppConfig:
        poll_interval = 2
        try:
            poll_interval = max(1, int(self.poll_interval_seconds.get().strip()))
        except ValueError:
            pass
        try:
            escpos_port = max(1, int(self.escpos_port.get().strip()))
        except ValueError:
            escpos_port = 9100
        try:
            escpos_order_text_size = max(1, int(self.escpos_order_text_size.get().strip()))
        except ValueError:
            escpos_order_text_size = 2
        try:
            escpos_table_text_size = max(1, int(self.escpos_table_text_size.get().strip()))
        except ValueError:
            escpos_table_text_size = 3
        return AppConfig(
            backend_url=self.backend_url.get().strip(),
            event_id=self.event_id.get().strip(),
            station_code=self.station_code.get().strip().lower(),
            access_token=self.access_token.get().strip(),
            printer_secret=self.printer_secret.get().strip(),
            agent_name=self.agent_name.get().strip() or "printer-agent",
            poll_interval_seconds=poll_interval,
            printer_mode=self.printer_mode.get().strip() or "preview",
            printer_command=self.printer_command.get(),
            output_path=self.output_path.get().strip(),
            escpos_host=self.escpos_host.get().strip(),
            escpos_port=escpos_port,
            escpos_order_text_size=escpos_order_text_size,
            escpos_table_text_size=escpos_table_text_size,
            escpos_cut_type=normalize_escpos_cut_type(self.escpos_cut_type.get().strip()),
        )

    def _save_clicked(self) -> None:
        config = self._current_config()
        save_config(config)
        self._log("Saved config")
        self._schedule_health_check(0)

    def _start_clicked(self) -> None:
        config = self._current_config()
        missing = validate_required_config(config)
        if missing:
            messagebox.showerror("Missing configuration", f"Please fill: {', '.join(missing)}")
            return
        if self.worker and self.worker.is_alive():
            self._log("Service already running.")
            return

        save_config(config)
        self.stop_event.clear()
        self.status_text.set(f"Running for station '{config.station_code}'")
        self._set_running_ui(True)
        self.worker = threading.Thread(target=self._run_worker, args=(config,), daemon=True)
        self.worker.start()
        self._schedule_health_check(0)
        self._log("Printer service started.")

    def _stop_clicked(self) -> None:
        self.stop_event.set()
        self.status_text.set("Stopping...")
        self.stop_button.configure(state="disabled")
        self._log("Stop requested.")

    def _test_print_clicked(self) -> None:
        config = self._current_config()
        item_line = TicketPrinter._format_item_line(1, "Testgericht", "10.00", "10.00")
        sample = (
            f"{config.station_code.upper() or 'STATION'}\n"
            "TESTDRUCK\n\n"
            "Bestellung #999\n"
            "Tisch Test\n"
            "Kellner QA\n"
            f"{item_line}\n"
            "  Notiz: Druckerservice pruefen\n"
        )
        job = {
            "payload_json": {
                "station_name": config.station_code.upper() or "STATION",
                "station_code": config.station_code,
                "order_number": 999,
                "table_label": "Test",
                "waiter_short_name": "QA",
                "created_at": time.strftime("%d-%m-%Y %H:%M:%S"),
                "job_type": "new_order",
                "items": [
                    {
                        "quantity": 1,
                        "menu_item_name": "Testgericht",
                        "unit_price": "10.00",
                        "total_price": "10.00",
                        "note": "Druckerservice pruefen",
                    }
                ],
            }
        }
        try:
            printer = TicketPrinter(config)
            result = printer.print_job(job, sample, "test-job")
            self._set_preview(sample)
            self._log(result)
            self._schedule_health_check(0)
        except Exception as exc:
            self._log(f"Test print failed: {exc}")

    def _check_status_clicked(self) -> None:
        self._schedule_health_check(0)

    def _schedule_health_check(self, delay_ms: int) -> None:
        if self.closed:
            return
        if self.health_after_id is not None:
            try:
                self.root.after_cancel(self.health_after_id)
            except TclError:
                pass
        self.health_after_id = self.root.after(delay_ms, self._start_health_check)

    def _start_health_check(self) -> None:
        self.health_after_id = None
        if self.closed:
            return
        if self.health_check_running:
            self._schedule_health_check(1000)
            return

        self.health_check_running = True
        if self.backend_status_text.get() == "Backend: unchecked":
            self._set_status_badge(
                self.backend_status_badge,
                self.backend_status_text,
                "Backend: checking",
                STATUS_WAITING,
            )
        if self.printer_status_text.get() == "Printer: unchecked":
            self._set_status_badge(
                self.printer_status_badge,
                self.printer_status_text,
                "Printer: checking",
                STATUS_WAITING,
            )
        config = self._current_config()
        thread = threading.Thread(target=self._run_health_check, args=(config,), daemon=True)
        thread.start()

    def _run_health_check(self, config: AppConfig) -> None:
        backend_status = check_backend_health(config)
        printer_status = check_printer_health(config)
        if not self.closed:
            self.root.after(0, lambda: self._apply_health_results(backend_status, printer_status))

    def _apply_health_results(self, backend_status: HealthStatus, printer_status: HealthStatus) -> None:
        self.health_check_running = False
        self._set_status_badge(
            self.backend_status_badge,
            self.backend_status_text,
            f"Backend: {backend_status.message}",
            STATUS_OK if backend_status.ok else STATUS_BAD,
        )
        self._set_status_badge(
            self.printer_status_badge,
            self.printer_status_text,
            f"Printer: {printer_status.message}",
            STATUS_OK if printer_status.ok else STATUS_BAD,
        )
        self._schedule_health_check(5000)

    def _set_status_badge(self, badge: Label, text: StringVar, message: str, color: str) -> None:
        if text.get() == message and badge.cget("bg") == color:
            return
        text.set(message)
        badge.configure(bg=color, fg="white")

    def _run_worker(self, config: AppConfig) -> None:
        run_worker_loop(
            config,
            stop_requested=self.stop_event.is_set,
            log_callback=self._log,
            preview_callback=self._set_preview,
        )
        if not self.closed:
            self.root.after(0, self._service_stopped)

    def _service_stopped(self) -> None:
        self.status_text.set("Stopped")
        self._set_running_ui(False)
        self._schedule_health_check(0)
        self._log("Printer service stopped.")

    def _set_preview(self, ticket_text: str) -> None:
        if not self.closed:
            self.root.after(0, lambda: self._replace_text(self.preview_text, ticket_text))

    def _replace_text(self, widget: Text, content: str) -> None:
        widget.delete("1.0", END)
        widget.insert("1.0", content)

    def _log(self, message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        self.log_queue.put(f"[{timestamp}] {message}")

    def _drain_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert(END, message + "\n")
            self.log_text.see(END)
        if not self.closed:
            self.root.after(200, self._drain_logs)

    def _on_close(self) -> None:
        self.closed = True
        self.stop_event.set()
        if self.health_after_id is not None:
            try:
                self.root.after_cancel(self.health_after_id)
            except TclError:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    PrinterServiceApp().run()


if __name__ == "__main__":
    main()
