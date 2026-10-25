#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: sudo gateway/install.sh --device /dev/serial/by-id/YOUR_ADAPTER [--port 4196]"
  echo "       [--enable-onewire] [--onewire-port 4197]"
}

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

device=""
port="4196"
enable_onewire=0
onewire_port="4197"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --device)
      device=${2:-}
      shift 2
      ;;
    --port)
      port=${2:-}
      shift 2
      ;;
    --enable-onewire)
      enable_onewire=1
      shift
      ;;
    --onewire-port)
      onewire_port=${2:-}
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z ${device} || ${device} != /dev/serial/by-id/* ]]; then
  echo "--device must be a stable /dev/serial/by-id/... path." >&2
  exit 2
fi
if [[ ! ${port} =~ ^[0-9]+$ ]] || (( port < 1 || port > 65535 )); then
  echo "--port must be between 1 and 65535." >&2
  exit 2
fi
if [[ ! ${onewire_port} =~ ^[0-9]+$ ]] || (( onewire_port < 1 || onewire_port > 65535 )); then
  echo "--onewire-port must be between 1 and 65535." >&2
  exit 2
fi

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

apt-get update
apt-get install -y python3 python3-venv python3-pip

passivelink_groups="dialout"
if [[ ${enable_onewire} -eq 1 ]]; then
  # video: /dev/vcio access for vcgencmd (Pi diagnostics: throttled, temp, voltage).
  passivelink_groups="dialout,video"
fi
if ! id passivelink >/dev/null 2>&1; then
  useradd --system --home /opt/dantherm-passivelink \
    --shell /usr/sbin/nologin --groups "${passivelink_groups}" passivelink
else
  usermod -aG "${passivelink_groups}" passivelink
fi

install -d -o passivelink -g passivelink /opt/dantherm-passivelink
install -d -o root -g passivelink -m 0750 /etc/dantherm-passivelink
install -o passivelink -g passivelink -m 0755 \
  "${script_dir}/passivelink_gateway.py" \
  /opt/dantherm-passivelink/passivelink_gateway.py
install -o passivelink -g passivelink -m 0644 \
  "${script_dir}/temperature_snapshot.py" \
  /opt/dantherm-passivelink/temperature_snapshot.py

if [[ ! -x /opt/dantherm-passivelink/venv/bin/python ]]; then
  runuser -u passivelink -- python3 -m venv /opt/dantherm-passivelink/venv
fi
runuser -u passivelink -- \
  /opt/dantherm-passivelink/venv/bin/pip install --upgrade pip
runuser -u passivelink -- \
  /opt/dantherm-passivelink/venv/bin/pip install pyserial==3.5

install -o root -g root -m 0644 \
  "${script_dir}/dantherm-passivelink.service" \
  /etc/systemd/system/dantherm-passivelink.service

config_file=/etc/dantherm-passivelink/gateway.env
if [[ -e ${config_file} ]]; then
  cp --archive "${config_file}" "${config_file}.previous"
fi
{
  printf 'RS485_DEVICE=%s\n' "${device}"
  printf 'GATEWAY_BIND=0.0.0.0\n'
  printf 'GATEWAY_PORT=%s\n' "${port}"
} > "${config_file}"
chown root:passivelink "${config_file}"
chmod 0640 "${config_file}"

systemctl daemon-reload
systemctl enable --now dantherm-passivelink.service
systemctl restart dantherm-passivelink.service

echo
echo "Dantherm Control gateway installed."
systemctl --no-pager --full status dantherm-passivelink.service || true

if [[ ${enable_onewire} -eq 1 ]]; then
  install -o passivelink -g passivelink -m 0755 \
    "${script_dir}/onewire_temperature_server.py" \
    /opt/dantherm-passivelink/onewire_temperature_server.py
  install -o root -g root -m 0644 \
    "${script_dir}/dantherm-passivelink-onewire.service" \
    /etc/systemd/system/dantherm-passivelink-onewire.service

  onewire_config=/etc/dantherm-passivelink/onewire.json
  if [[ ! -e ${onewire_config} ]]; then
    install -o root -g passivelink -m 0640 \
      "${script_dir}/onewire.example.json" \
      "${onewire_config}"
  fi

  # --port is baked into the unit file's ExecStart; regenerate it when the
  # caller picks a non-default --onewire-port.
  sed -i "s/--port 4197/--port ${onewire_port}/" \
    /etc/systemd/system/dantherm-passivelink-onewire.service

  systemctl daemon-reload
  systemctl enable --now dantherm-passivelink-onewire.service
  systemctl restart dantherm-passivelink-onewire.service

  echo
  echo "Optional DS18B20 temperature/diagnostics service installed."
  systemctl --no-pager --full status dantherm-passivelink-onewire.service || true

  if ! lsmod | grep -q '^w1_gpio'; then
    echo
    echo "NOTE: the w1-gpio kernel module is not loaded yet."
    echo "Enable 1-Wire on GPIO4 and reboot before the DS18B20 sensors will show up:"
    echo "  echo 'dtoverlay=w1-gpio,gpiopin=4' | sudo tee -a /boot/firmware/config.txt"
    echo "  sudo reboot"
    echo "(Netboot/custom setups: add the same line to whichever config.txt your"
    echo "board actually boots from - see docs/raspberry-pi-gateway.*.md.)"
  fi
fi
