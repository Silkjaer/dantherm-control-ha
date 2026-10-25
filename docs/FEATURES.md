# Dantherm Control for Home Assistant: feature list

*Formerly Dantherm HCH5 Control.*

This describes the published **0.8.1-beta.17** integration for a Dantherm HCH5 MK1 with HAC1. The [animated tour](images/0.8.1-beta.8/ha-integration-tour.gif) is an illustration with example values, not a capture of a Home Assistant installation. The [Danish entity reference](entities.da.md) explains individual sensors and their limitations.

## Read the ventilation unit

- Connect to a raw RS485-over-TCP stream, including the Dantherm Control Raspberry Pi gateway on port `4196`, or listen through a USB-RS485 adapter attached directly to the Home Assistant host.
- Decode observed HCH5/HAC1 temperatures, CO₂, humidity, fan speed and control percentages, operating mode, ventilation level, bypass, afterheat, fireplace, night and standby states. The telemetry connection does not transmit control writes to the RS485 bus.
- Expose heat-recovery efficiency, its learned reference and decline status, air-quality classification, fan differences and RS485 frame rate. Raw register values are diagnostic entities and are disabled by default where their meaning is not verified.
- Track filter interval, remaining days, status, local reset and the latest 20 confirmed changes. Optional Home Assistant persistent and mobile notifications can warn once per filter cycle at a chosen threshold.
- Create and clear a Home Assistant Repairs issue when the gateway or HAC1 stays unavailable for more than 15 minutes.

## Control through the Raspberry Pi API

Controller entities appear only when the separate Dantherm Control Raspberry Pi API is configured with its host, port and token. Home Assistant sends high-level commands to that API; the Pi checks master arbitration and performs verified writes. The Pi remains the source of truth, and settings changed in its WebUI return to Home Assistant on the next poll.

- A fan entity offers Local Auto, Smart Auto, manual levels 1–5 and Boost. Separate selects expose mode and levels **OFF–6**. The OFF option uses the Pi's standby control; another select offers 1, 4 or 8 hours, until 07:00, or permanent OFF until restarted.
- Buttons request Quick Boost for 15, 30 or 60 minutes and can cancel it. Fireplace duration, bonfire mode and free-cooling controls are exposed; an external fireplace-signal switch can drive the Pi's automatic fireplace mode when enabled there.
- A bypass select sends the request through the Pi API; separate readbacks show the actual damper state and travel. A climate entity exposes afterheat ON/OFF and its 10–35 °C supply-air setpoint; HAC1 still regulates its own valve and protection.
- Number entities cover CO₂/RH targets, hysteresis, step size, local minimum/normal/maximum levels, downshift and boost timing, Home Assistant timeout, and separate supply/extract percentages for all six fan profiles.
- Air-balance switch, Auto/fixed duct-ratio select and target numbers expose the Pi's balancing settings. Diagnostic entities show the ratio in use, learned heat-balance information, present extract excess and any balancing error. The estimates are not a substitute for measuring room airflow during commissioning.
- Status entities show active master, effective level and reason, Smart Auto's controlling room and measurement, stale-input age, hardware-write state, HCP4 activity, fan readbacks, afterheat, frost and other controller diagnostics.

## Bring Home Assistant sensors into Smart Auto

- Configure up to 32 rooms with a name, type (`auto`, `normal`, `bathroom`), priority and control/monitor-only setting. Choose optional temperature, humidity, CO₂ and PM2.5 sensors per room.
- Entity pickers list supported units and device classes for each field. Unknown, unavailable and out-of-range values are omitted instead of making the Pi reject the full update. The sent room data includes the source entity IDs.
- The Pi combines valid room values with its own sensors. Leased inputs expire when Home Assistant stops sending them; the Pi then falls back to local inputs. PM2.5 support requires a compatible Pi controller; Pi version 1.3.3 and newer include it.

## Weather from Home Assistant

- Select a `weather.*` entity for current conditions in the Pi WebUI. The HCH5's measured T1 remains the displayed outdoor temperature.
- Send current temperature, humidity and dew point on a five-minute lease. The Pi can use humidity in its existing drying decision only when explicitly enabled there and when the source agrees with fresh T1 within 6 °C. Missing or stale data falls back to local control.

## Energy, prices and optional Pi sensors

- Select a live unit power sensor, a **daily** unit-energy sensor, electricity price and heat price. W/kW, Wh/kWh/MWh and supported kr/DKK/øre price units are converted before they are sent to the Pi. Stale data expires there.
- Expose Pi-provided cumulative recovered-heat, air-side afterheat and unit-electricity kWh for Home Assistant statistics and dashboards, plus today’s values, power, SFP and filter-power indicator when the necessary inputs exist.
- Distinguish a measured unit electricity meter from the Pi's estimate. Recovered and afterheat energy are air-side estimates; a heat-price calculation is theoretical, and current prices do not reconstruct historical tariffs.
- Optional DS18B20 flow/return sensors create a separate water-temperature device. Enabling the Pi extension also exposes host diagnostics such as CPU temperature, voltage, load, memory, disk and undervoltage/throttling flags. Missing optional probes do not stop the main HCH5 entities.

## Scope and compatibility

- Works with HCH5 **MK1 with HAC1**. HCH5 MKII and official Modbus TCP units are outside the documented compatibility of 0.8.1-beta.17; version 0.9.0 (beta) adds Dantherm units with the Dantherm Residential Modbus map (HCH5 MKII, RCV, HCV, HCC) over Modbus TCP, see the [README](../README.md#dantherm-units-over-modbus-tcp-beta).
- The classic RS485 listener can be used without Pi control. Controller entities, room forwarding, balancing and Pi energy data require the separate [Dantherm Control Pi app](https://github.com/MRDonnii/dantherm-control) installation and authenticated API.
- The weekly planner, user accounts and SMTP alerts belong to the Pi WebUI; they are not configuration pages in this Home Assistant integration.
