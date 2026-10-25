# Low-cost Raspberry Pi RS485 gateway

This guide builds a resilient, receive-only RS485-to-Ethernet bridge for a
Dantherm HCH5 MK1. It has been tested with a Raspberry Pi 2 Model B and an
FTDI-based USB-RS485 adapter. Newer Raspberry Pi models can also be used.

The bridge is not a Modbus master. It only mirrors bytes already present on
the bus, while the original Dantherm controller remains connected and in
control. This is an unofficial community project and is not developed,
approved, certified or supported by Dantherm Group.

## Hardware

- Raspberry Pi 2 Model B or newer
- 8 GB or larger microSD card
- stable power supply suitable for the Pi model
- wired Ethernet connection
- Linux-compatible USB-RS485 adapter supporting `19200 8E1`
- twisted pair for RS485 A/B
- optional: two waterproof DS18B20 probes and one 4.7 kΩ resistor

A galvanically isolated USB-RS485 adapter is preferable for a permanent
installation. Keep the existing Dantherm RS485 wiring intact and attach the
receiver in parallel. Do not rely on conductor colours. If no valid frames are
seen, power down and swap A/B at one end only. Do not add another 120 Ω
terminator unless the actual bus topology requires it.

## Operating system and network

Use **Raspberry Pi OS Lite (32-bit)**. Enable SSH in Raspberry Pi Imager and
choose a unique username and strong password. Connect Ethernet, boot, then:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Reserve the Pi address in your router. This is preferred to embedding a static
address in the Pi. If a local static address is required, use NetworkManager
and substitute values appropriate for your own network:

```bash
nmcli connection show
sudo nmcli connection modify "Wired connection 1" \
  ipv4.method manual \
  ipv4.addresses PI_ADDRESS/CIDR \
  ipv4.gateway ROUTER_ADDRESS \
  ipv4.dns DNS_ADDRESS
sudo nmcli connection up "Wired connection 1"
```

## Install the gateway

Find the stable USB path:

```bash
lsusb
ls -l /dev/serial/by-id/
```

Clone and install the companion project:

```bash
git clone https://github.com/MRDonnii/dantherm-control-ha.git
cd dantherm-control-ha
sudo gateway/install.sh --device /dev/serial/by-id/YOUR_ADAPTER
```

The installer enables automatic startup and restart after service failure,
USB reconnect or power loss. Check it with:

```bash
systemctl is-active dantherm-passivelink.service
journalctl -u dantherm-passivelink.service -n 50 --no-pager
ss -lntp | grep 4196
```

Add Dantherm Control in Home Assistant, choose
**HCH5 MK1 + HAC1 – RS485 over TCP**, and enter the Pi address and port `4196`. The address and port can later be changed
with the integration's **Configure** action without recreating entities.

Never port-forward TCP 4196 to the internet. If a firewall is enabled, allow
only the Home Assistant host.

## Hardware watchdog

Enable the Pi hardware watchdog in `/boot/firmware/config.txt`:

```ini
dtparam=watchdog=on
```

Create `/etc/systemd/system.conf.d/watchdog.conf`:

```ini
[Manager]
RuntimeWatchdogSec=30s
RebootWatchdogSec=2min
```

Then reboot and verify:

```bash
sudo systemctl daemon-reexec
sudo reboot
systemctl show --property=RuntimeWatchdogUSec
```

## Optional DS18B20 water temperatures

This extension is independent of the RS485 bridge. If it is absent or fails,
only its own entities become unavailable.

Connect both probes in parallel:

| DS18B20 | Raspberry Pi 2B |
| --- | --- |
| VCC | physical pin 1, 3.3 V |
| Data | physical pin 7, GPIO4 |
| GND | physical pin 6, ground |

Place one 4.7 kΩ pull-up resistor between Data/GPIO4 and 3.3 V. Never apply
5 V to GPIO4. Add `dtoverlay=w1-gpio,gpiopin=4` to
`/boot/firmware/config.txt`, reboot and list the probes:

```bash
ls -1 /sys/bus/w1/devices/28-*
```

Install the temperature service with the installer (safe to re-run with the
same `--device`):

```bash
sudo gateway/install.sh --device /dev/serial/by-id/YOUR_ADAPTER --enable-onewire
```

This also adds `passivelink` to the `video` group, needed to read Pi
diagnostics via `vcgencmd`. Use `--onewire-port` if 4197 is already taken. See
the [complete Danish guide](raspberry-pi-gateway.da.md) for the full manual
steps and the Raspberry Pi diagnostics device (CPU temperature, voltage,
undervoltage/throttling flags, memory and disk use). In the Home Assistant
integration options, enable the water-preheater extension and enter port
`4197`. Flow and return can be swapped in the same options screen without
rewiring; the Raspberry Pi diagnostics device is created automatically once
this option is enabled, even without DS18B20 probes attached.

## Troubleshooting

- No serial device: inspect `lsusb` and `/dev/serial/by-id/`.
- Permission denied: confirm that `passivelink` belongs to `dialout`.
- No frames: verify `19200 8E1`, wiring polarity and that the original
  controller still communicates.
- HA cannot connect: check `ss -lntp`, firewall rules and the configured Pi
  address.
- Recent errors: run
  `journalctl -u dantherm-passivelink.service -n 100 --no-pager`.
