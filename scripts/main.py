#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import yaml
import logging

from collector import read
from calculations import (
    delta_t,
    power_kw,
    gas_m3_h,
    efficiency
)
from diagnostics import analyse
from taktowanie import register
from condensation import is_condensing
from curve_recommendation import recommend
from rrd_store import update
from mqtt_publish import send


# --------------------------------------------------
# KONFIGURACJA
# --------------------------------------------------

BASE_PATH = "/opt/ebus/ebus-thelia-condens-ha"

EBUS_CFG = f"{BASE_PATH}/config/ebus.yaml"
MQTT_CFG = f"{BASE_PATH}/config/mqtt.yaml"

INTERVAL = 60  # sekundy


# --------------------------------------------------
# LOGGING
# --------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

log = logging.getLogger("ebus-main")


# --------------------------------------------------
# LOAD CONFIG
# --------------------------------------------------

with open(EBUS_CFG) as f:
    ebus_cfg = yaml.safe_load(f)

with open(MQTT_CFG) as f:
    mqtt_cfg = yaml.safe_load(f)


# --------------------------------------------------
# MAIN LOOP
# --------------------------------------------------

def main():
    log.info("eBUS monitoring started (READ-ONLY mode)")

    while True:
        try:
            # -------------------------------
            # ODCZYT eBUS
            # -------------------------------
            flow = read(ebus_cfg["commands"]["flow_temp"])
            ret = read(ebus_cfg["commands"]["return_temp"])
            mod = read(ebus_cfg["commands"]["burner_modulation"])
            burner = read(ebus_cfg["commands"]["burner_state"])

            # -------------------------------
            # OBLICZENIA
            # -------------------------------
            delta = delta_t(flow, ret)
            power = power_kw(mod)
            gas = gas_m3_h(power)
            eff = efficiency(delta)

            # -------------------------------
            # ANALIZA EKSPERCKA
            # -------------------------------
            starts = register(burner)
            condensing = is_condensing(ret, delta)

            diag = analyse(delta, mod)
            curve = recommend(
                delta=delta,
                modulation=mod,
                taktowanie=(starts > 6),
                condensing=condensing
            )

            # -------------------------------
            # RRD TOOL
            # -------------------------------
            update(
                flow=flow,
                ret=ret,
                delta=delta,
                burner=burner,
                modulation=mod
            )

            # -------------------------------
            # MQTT PUBLISH
            # -------------------------------
            payload = {
                "flow_temp": round(flow, 2),
                "return_temp": round(ret, 2),
                "delta_t": round(delta, 2),
                "power_kw": round(power, 2),
                "gas_m3_h": round(gas, 4),
                "efficiency_pct": eff,
                "burner_state": int(burner),
                "burner_starts_h": starts,
                "condensing": int(condensing),

                # diagnostyka
                "diagnostic_level": diag["level"],
                "diagnostic_msg": diag["msg"],

                # rekomendacje
                "curve_recommendation": curve
            }

            send(mqtt_cfg["base_topic"], payload)

            log.info(
                "ΔT=%.1f°C | Mod=%.0f%% | Eff=%s%% | Gas=%.3f m3/h | Diag=%s",
                delta, mod, eff, gas, diag["level"]
            )

        except Exception as e:
            log.error("Runtime error: %s", e, exc_info=True)

        time.sleep(INTERVAL)


# --------------------------------------------------
# ENTRYPOINT
# --------------------------------------------------

if __name__ == "__main__":
    main()
