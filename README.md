# Printer Service

Standalone printer agent for OrderSystem.

Available variants:
- GUI: `printerService/main.py` with Tkinter configuration window
- Headless: `printerService/headless.py` for CLI / service usage

What it does:
- polls the backend for print jobs for one event and one station
- prints jobs locally on Linux, Windows, or Raspberry Pi
- supports `preview`, `file`, `command`, and built-in `escpos-network` printer modes
- acknowledges completed or failed jobs back to the backend

Run GUI locally:

```bash
python3 printerService/main.py
```

Run multiple GUI instances on the same device:

```bash
python3 printerService/main.py --instance-name kitchen
python3 printerService/main.py --instance-name bar
```

Run headless locally (Use quotes for params): 

```bash
python3 printerService/headless.py --backend-url <printerServiceURL> --event-id <event-id> --station-code <stationCode> --oidc-token-url <keycloak-token-url> --oidc-client-id <client-id> --oidc-client-secret <client-secret> --printer-mode escpos-network --escpos-host <printerIP> --escpos-port 9100
```

Run multiple headless instances on the same device:

```bash
python3 printerService/headless.py --instance-name kitchen --station-code kitchen --save-config
python3 printerService/headless.py --instance-name bar --station-code bar --save-config
```

Bundle GUI as standalone:

Linux / Raspberry Pi:

```bash
chmod +x printerService/build-linux.sh
./printerService/build-linux.sh
```

On Raspberry Pi OS / Debian, the script creates and uses a local virtualenv automatically. If that support is missing, install it first:

```bash
sudo apt install python3-venv
```

Windows:

```bat
printerService\build-windows.bat
```

Bundle headless as standalone:

Linux / Raspberry Pi:

```bash
chmod +x printerService/build-linux-headless.sh
./printerService/build-linux-headless.sh
```

Windows:

```bat
printerService\build-windows-headless.bat
```

Build output:
- GUI Linux / Raspberry Pi: `dist/OrderSystemPrinterService`
- GUI Windows: `dist/OrderSystemPrinterService.exe`
- Headless Linux / Raspberry Pi: `dist/OrderSystemPrinterServiceHeadless`
- Headless Windows: `dist/OrderSystemPrinterServiceHeadless.exe`

Important:
- build on the target OS you want to run on
- Windows builds should be created on Windows
- Linux / Raspberry Pi builds should be created on the same architecture family you plan to run
- PyInstaller does not reliably cross-build Windows from Linux or ARM from x86
- for Windows builds, use Python 3.12



Recommended configuration:
- `Backend URL`: the order System URL
  Use the public site base URL such as `https://os2.roflcode.com`, not `.../api`.
  The printer service talks to the public `/health` and `/print-service/*` endpoints.
- `Event ID`: the event UUID
- `Station Code`: for example `kitchen` or `bar`
- `Keycloak Token URL`: usually `https://<your-host>/keycloak/realms/<realm>/protocol/openid-connect/token`
- `Keycloak Client ID`: the confidential Keycloak client used for this printer service
- `Keycloak Client Secret`: the client secret for that Keycloak client
- create the printer client in Keycloak as a confidential client with service accounts enabled
- assign the printer client the `printer` role expected by the backend
- `Printer Mode`:
  - `preview`: no physical printing, shows the last ticket in the app
  - `file`: appends tickets to the configured file
  - `command`: runs a local print command such as `lp {file}` or `powershell -File print.ps1 {file}`
  - `escpos-network`: sends ESC/POS bytes directly to a network bon printer, typically Epson-compatible on port `9100`

Built-in bon printer support:
- `escpos-network` is meant for common Epson-compatible receipt printers and many ESC/POS clones
- configure:
  - `ESC/POS Host`
  - `ESC/POS Port` usually `9100`
  - `Order Text Size`
  - `Table Text Size`
- `Table Text Size` controls how large the table number is printed
- `Order Text Size` controls how large the order number is printed

Headless usage notes:
- use `--instance-name <name>` to keep a separate config per local printer instance
- use `--config-path <path>` if you want full control over where one instance stores its config
- CLI arguments override values from `~/.ordersystem_printer_service.json`
- use `--save-config` to persist the effective config before starting
- use `--test-print` to test local output without talking to the backend
- the headless variant writes logs to stdout and is suitable for `systemd`, NSSM, Task Scheduler, or Docker

Notes:
- `command` mode replaces `{file}`, `{job_id}`, and `{station_code}` in the configured command string
- the app stores its config in `~/.ordersystem_printer_service.json`
- named instances use files like `~/.ordersystem_printer_service_kitchen.json`
- on Raspberry Pi, `command` mode with `lp {file}` is a simple way to integrate with CUPS
- the printer service now authenticates with Keycloak client credentials and sends a bearer token to `/print-service/*`
- the backend must be configured with `PRINTER_OIDC_ISSUER`, `PRINTER_OIDC_JWKS_URL`, and the required printer role
- the cut type option was removed from GUI and headless startup because this printer setup does not expose a meaningful choice there
- the built-in `escpos-network` mode currently targets raw TCP receipt printers; USB/vendor-driver printing is not built in yet
