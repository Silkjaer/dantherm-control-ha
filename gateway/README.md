# Raspberry Pi RS485 gateway

This folder is a self-contained companion project for
[Dantherm Control](../README.md). It turns a Raspberry Pi with a
USB-RS485 adapter into a receive-only RS485-to-Ethernet bridge.

The bridge mirrors observed `19200 8E1` bytes to raw TCP port `4196`. It does
not generate Modbus requests or write to the serial port by default. An optional
temperature-snapshot mode sends only the fixed read request described below.
The existing Dantherm controller remains in charge; there are no register writes.

## Synchronized temperature snapshots (opt-in)

On the verified HCH5 MK1/HAC1 installation, a single FC03 request to slave `0x40`,
registers `180–209`, returns T1–T4, HRC2/T5, T2AH and TFAH. Snapshot mode repeats
this read approximately every 10 seconds and pairs the request with its checked
response in the TCP stream. It waits for a bus gap and skips busy/failed cycles.

This is **active polling**, not passive listening. Verify bus compatibility before
enabling it; do not run another gateway/serial owner in parallel. Direct USB mode
in Home Assistant remains passive and does not offer this polling mode.

After updating the gateway and integration, add `TEMPERATURE_SNAPSHOTS=1` to
`/etc/dantherm-passivelink/gateway.env`, then restart `dantherm-passivelink.service`.
For a manual launch, use `--temperature-snapshots`. Remove the setting to return
to passive operation. Re-running the installer resets the generated env file;
re-enable the option after reviewing the installation. Restart Home Assistant
after installing the changed Python integration files.

The integration commits the seven temperatures together, ignores older separate
temperature frames after receiving a snapshot, and invalidates them after 30
seconds without a new snapshot while other bus traffic continues. Sensor fault
sentinels become unavailable, not a cached previous reading. With the optional
water-probe service enabled, it fetches flow/return in the same update round and
publishes them with the air temperatures; a failed water fetch does not hide
valid air data. Separate sensors can still have different internal sample times.

This changes data acquisition only: it does not calibrate sensors, override
actuators, change heater setpoints, or bypass temperature protections. TFAH is a
water/frost sensor, not the air temperature before the heating coil.

## Included files

| File | Purpose |
| --- | --- |
| `passivelink_gateway.py` | Receive-only serial-to-TCP bridge |
| `dantherm-passivelink.service` | Automatically starts and restarts the bridge |
| `gateway.example.env` | Adapter path, bind address and TCP port |
| `install.sh` | Idempotent installer for Raspberry Pi OS Lite |
| `onewire_temperature_server.py` | Optional DS18B20 temperature service |
| `dantherm-passivelink-onewire.service` | Optional temperature service |
| `onewire.example.json` | Optional flow/return probe assignment |

## Quick start

Install Raspberry Pi OS Lite (32-bit), enable SSH and connect the Pi by
Ethernet. Clone this repository on the Pi, then run:

```bash
git clone https://github.com/MRDonnii/dantherm-control-ha.git
cd dantherm-control-ha
ls -l /dev/serial/by-id/
sudo gateway/install.sh --device /dev/serial/by-id/YOUR_ADAPTER
```

The installer creates an unprivileged `passivelink` account, a Python virtual
environment, `/etc/dantherm-passivelink/gateway.env`, and an enabled systemd
service. Verify it with:

```bash
systemctl status dantherm-passivelink.service --no-pager
journalctl -u dantherm-passivelink.service -n 50 --no-pager
ss -lntp | grep 4196
```

Then add the Home Assistant integration in **HCH5 MK1 + HAC1 – RS485 over TCP**
mode and enter the Raspberry Pi address and port `4196`.

For wiring, fixed IP configuration, watchdog, firewall, troubleshooting and
the optional water-preheater probes, follow the complete guides:

- [Dansk trin-for-trin-guide](../docs/raspberry-pi-gateway.da.md)
- [English step-by-step guide](../docs/raspberry-pi-gateway.en.md)

## Safety

This unofficial project is not affiliated with or supported by Dantherm
Group. Power down equipment before changing wiring. Keep the existing
controller connection intact, do not add an unnecessary bus terminator, and
never expose the raw TCP port to the internet.
