# eBUS Thelia Condens Home Assistant Bridge

Python tools for reading eBUS traffic from a Saunier Duval Thelia Condens boiler, decoding a focused set of telegrams, and publishing the resulting telemetry to Home Assistant over MQTT.

## What the repository does

- Reads raw eBUS frames from a serial adapter at 2400 baud.
- Parses known Thelia and MiPro telegrams, especially `B504`, `B509`, `B510`, `B511`, `B512`, `B516`, and `0704`.
- Builds a live sensor model with freshness tracking, burner runtime counters, and persisted flame-cycle state.
- Publishes Home Assistant MQTT discovery plus sensor state topics under `ebus/thelia/...`.
- Actively polls the boiler for status, temperatures, and modulation when passive traffic is stale.
- Reconnects after serial silence and can optionally run an external adapter reset command after repeated idle disconnects.

## Main entrypoints

- `main_service.py`
  Long-running Home Assistant bridge. This is the most complete runtime path in the repo.
- `main.py`
  Simpler YAML-configured reader that logs parsed telegrams and current parser statistics.
- `tools/capture.py`
  Capture and reverse-engineering helper for raw or parsed eBUS traffic.

## Telemetry currently exposed

- Boiler flow temperature
- Boiler return temperature
- DHW tank temperature
- Outdoor temperature
- Room temperature from boiler and MiPro controller
- Water pressure
- Burner modulation and burner power alias
- Boiler state code
- Pump status
- Heating active and DHW active flags
- Flame state
- Burner starts today, last 24h, and last 7d
- Burner runtime totals and last cycle duration
- Freshness metrics such as `boiler.status_stale`, `boiler.status_last_update_s`, and `boiler.ebus_last_seen_s`
- A few raw helper bytes for ongoing reverse engineering

## Repository layout

- `ebus_core/`
  Low-level CRC, telegram parsing, serial I/O, and query sending.
- `thelia/`
  Thelia-specific message definitions, parser/aggregator logic, MQTT publishing, and adapter reset helpers.
- `config/config.yaml`
  Config file used by the simple reader in `main.py`.
- `tests/`
  Automated tests plus a few manual/debug helper scripts.
- `services/`
  Placeholder service modules that are not the main runtime path today.

## Requirements

- Python 3.9+
- A working eBUS serial adapter
- Linux or Raspberry Pi style serial access for the default runtime setup
- An MQTT broker if you want Home Assistant integration

The bridge code imports `paho.mqtt.client` directly. If your environment does not already include `paho-mqtt`, install it explicitly.

## Installation

Create a virtual environment and install the project:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pip install paho-mqtt
```

If you are working on Windows, activate the virtual environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

If you prefer the legacy requirements file, install with:

```bash
pip install -r requirements.txt
```

## Configuration

There are currently two configuration styles in the repo.

### 1. Simple reader configuration

`main.py` loads `config/config.yaml`. The shipped example includes:

```yaml
connection:
  port: /dev/ttyAMA0
  baudrate: 2400
  timeout: 0.1
  reconnect_delay: 5.0

parser:
  max_age: 300

logging:
  level: INFO
```

### 2. Home Assistant bridge configuration

`main_service.py` currently uses module-level constants for its main runtime settings. Before deploying it, review and update:

- `MQTT_BROKER`
- `MQTT_PORT`
- `MQTT_USER`
- `MQTT_PASS`
- `SERIAL_PORT`
- `RUNTIME_STATE_FILE`
- polling and reconnect intervals near the top of the file

The runtime state file stores burner start and runtime counters so they survive restarts.

## Running the project

### Simple parser/logger

```bash
python main.py
```

If installed in editable mode, the equivalent console script is:

```bash
ebus-reader
```

### Home Assistant MQTT bridge

```bash
python main_service.py
```

What this service does at runtime:

- reads passive eBUS traffic
- publishes Home Assistant discovery automatically
- republishes live sensor values every few seconds
- actively polls `B511/00`, `B511/01`, and `B511/02` when status data becomes stale
- reconnects the serial link after long periods of bus silence

### Traffic capture and reverse engineering

```bash
ebus-capture -m monitor
ebus-capture -m telegrams -c 50
ebus-capture -m raw -d 120 -o cap.bin
```

You can also run the tool directly:

```bash
python tools/capture.py -m monitor
```

## MQTT and Home Assistant behavior

The MQTT bridge publishes:

- entity discovery topics under `homeassistant/...`
- sensor state topics under `ebus/thelia/<sensor_key>`
- bridge availability under `ebus/thelia/status`
- a heartbeat topic under `ebus/thelia/bridge_heartbeat`

Discovery is sent automatically when the MQTT connection comes up. Known sensors have curated metadata, and unexpected sensor keys can still be published with inferred Home Assistant config.

## Optional adapter reset hook

When the serial bus goes silent for too long, `main_service.py` can escalate from reconnecting the serial port to running an external shell command that power-cycles or resets the adapter.

Supported environment variables:

- `EBUS_ADAPTER_RESET_COMMAND`
- `EBUS_ADAPTER_RESET_AFTER_IDLE_DISCONNECTS`
- `EBUS_ADAPTER_RESET_COOLDOWN_SECONDS`
- `EBUS_ADAPTER_RESET_SETTLE_SECONDS`
- `EBUS_ADAPTER_RESET_TIMEOUT_SECONDS`

Example:

```bash
export EBUS_ADAPTER_RESET_COMMAND="/usr/local/bin/reset-ebus-adapter"
export EBUS_ADAPTER_RESET_AFTER_IDLE_DISCONNECTS=2
python main_service.py
```

The command is executed through `/bin/sh -lc`, so it can be a shell command or wrapper script.

## Testing

Run the automated tests with:

```bash
python -m pytest -q tests
```

The automated suite covers:

- low-level frame parsing and CRC handling
- serial query building and matching replies
- Thelia parser and aggregator regressions
- MQTT reconnect and publish-failure recovery
- main service polling and idle-recovery logic
- adapter reset controller behavior

## Current limitations

- `main_service.py` is configured in code rather than from YAML or environment variables, except for adapter reset options.
- The packaged metadata is enough for the base parser, but the MQTT bridge may still need `paho-mqtt` installed explicitly depending on how you set up the environment.
- The `database` section in `config/config.yaml` is not wired into the main bridge runtime today.
- Some files inside `tests/` are manual debugging scripts rather than normal pytest modules.

## License

MIT. See `LICENSE`.
