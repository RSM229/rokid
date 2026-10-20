#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run with sudo: sudo bash setup-r640.sh" >&2
  exit 1
fi

cloud=8.134.23.170
cloud_fingerprint='SHA256:18gRGsA5NhN+vcoRygmKGxdUPFHN/EeqyNwNSJPCZq8'
remote_user=rokid-r640-tunnel
remote_port=22220
local_user=${1:-baize}
if ! getent passwd "${local_user}" >/dev/null; then
  echo "Local account '${local_user}' was not found. Run: sudo bash setup-r640.sh YOUR_LOGIN" >&2
  exit 1
fi
[[ ${local_user} =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || { echo "Unsupported login name." >&2; exit 1; }
command -v systemctl >/dev/null && [[ -d /run/systemd/system ]] || { echo "A Linux system running systemd is required." >&2; exit 1; }
command -v sshd >/dev/null || { echo "Install and start openssh-server first." >&2; exit 1; }
if ! timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/22'; then
  echo "R640 SSH must listen on 127.0.0.1:22. Start ssh/sshd before running this script." >&2
  exit 1
fi
[[ -f /etc/ssh/ssh_host_ed25519_key.pub ]] || { echo "R640 SSH host key is missing." >&2; exit 1; }
local_home=$(getent passwd "${local_user}" | cut -d: -f6)
ssh_bin=$(command -v ssh)
ssh_keygen_bin=$(command -v ssh-keygen)
[[ -n ${ssh_bin} && -n ${ssh_keygen_bin} ]] || { echo "OpenSSH client is required." >&2; exit 1; }

ssh_dir=${local_home}/.ssh
install -d -m 700 -o "${local_user}" -g "$(id -gn "${local_user}")" "${ssh_dir}"
tunnel_key=${ssh_dir}/id_ed25519_r640_tunnel
if [[ ! -e ${tunnel_key} ]]; then
  runuser -u "${local_user}" -- "${ssh_keygen_bin}" -q -t ed25519 -N '' -C "r640-to-${cloud}" -f "${tunnel_key}"
fi
[[ -f ${tunnel_key}.pub ]] || { echo "Tunnel private key exists but its public key is missing: ${tunnel_key}.pub" >&2; exit 1; }
chown "${local_user}:$(id -gn "${local_user}")" "${tunnel_key}" "${tunnel_key}.pub"
chmod 600 "${tunnel_key}"
chmod 644 "${tunnel_key}.pub"

known_hosts=${ssh_dir}/known_hosts_r640_tunnel
scan=$(mktemp)
trap 'rm -f "${scan}"' EXIT
ssh-keyscan -T 8 -t ed25519 "${cloud}" 2>/dev/null >"${scan}"
if ! ssh-keygen -lf "${scan}" | grep -Fq "${cloud_fingerprint}"; then
  echo "Cloud SSH host-key fingerprint did not match the verified server; stopping." >&2
  echo "Expected: ${cloud_fingerprint}" >&2
  exit 1
fi
install -m 600 -o "${local_user}" -g "$(id -gn "${local_user}")" "${scan}" "${known_hosts}"

echo "Paste the Windows R640-only public key printed by setup-local-ssh.ps1:"
IFS= read -r workstation_key || true
read -r workstation_type workstation_data workstation_comment <<<"${workstation_key}"
if [[ ${workstation_type} != ssh-ed25519 || ! ${workstation_data} =~ ^[A-Za-z0-9+/]+={0,2}$ ]]; then
  echo "Expected one valid ssh-ed25519 public-key line." >&2
  exit 1
fi
workstation_tmp=$(mktemp)
printf '%s\n' "${workstation_key}" >"${workstation_tmp}"
ssh-keygen -lf "${workstation_tmp}" >/dev/null || { rm -f "${workstation_tmp}"; echo "Invalid public key." >&2; exit 1; }
rm -f "${workstation_tmp}"
if [[ ! -e ${ssh_dir}/authorized_keys ]]; then
  install -m 600 -o "${local_user}" -g "$(id -gn "${local_user}")" /dev/null "${ssh_dir}/authorized_keys"
fi
if ! grep -Fq "${workstation_data}" "${ssh_dir}/authorized_keys"; then
  cp -p "${ssh_dir}/authorized_keys" "${ssh_dir}/authorized_keys.before-r640-tunnel.$(date +%Y%m%d%H%M%S)"
  printf '\n%s\n' "${workstation_key}" >>"${ssh_dir}/authorized_keys"
fi
chown "${local_user}:$(id -gn "${local_user}")" "${ssh_dir}/authorized_keys"
chmod 600 "${ssh_dir}/authorized_keys"

unit=/etc/systemd/system/r640-reverse-ssh-tunnel.service
cat >"${unit}" <<UNIT
[Unit]
Description=Persistent reverse SSH tunnel for R640 access
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=${local_user}
ExecStart=${ssh_bin} -NT -i "${tunnel_key}" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=${known_hosts}" -R 127.0.0.1:${remote_port}:127.0.0.1:22 ${remote_user}@${cloud}
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
UNIT
chmod 644 "${unit}"
systemctl daemon-reload
systemctl enable r640-reverse-ssh-tunnel.service
systemctl restart r640-reverse-ssh-tunnel.service

echo
echo "R640 reverse tunnel service installed and enabled."
echo "The service retries until the cloud script authorizes this R640 key."
echo "R640 public key for setup-cloud.sh:"
cat "${tunnel_key}.pub"
echo "R640 SSH host-key fingerprint (compare on first Windows connection):"
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
