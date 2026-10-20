#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run as root: sudo bash setup-cloud.sh" >&2
  exit 1
fi

command -v sshd >/dev/null || { echo "OpenSSH server is required." >&2; exit 1; }
sshd_config=$(sshd -T)
if ! grep -Eq '^allowtcpforwarding (yes|remote)$' <<<"${sshd_config}"; then
  echo "sshd must permit reverse TCP forwarding (AllowTcpForwarding yes or remote)." >&2
  exit 1
fi
if grep -qx 'disableforwarding yes' <<<"${sshd_config}" || ! grep -qx 'pubkeyauthentication yes' <<<"${sshd_config}"; then
  echo "sshd must allow public-key login and must not disable forwarding." >&2
  exit 1
fi
if [[ $(ss -Hlnt 'sport = :22220' | wc -l) -gt 0 ]]; then
  echo "NOTICE: port 22220 is already listening. If this is not the existing R640 tunnel, stop and resolve the conflict."
fi

account=rokid-r640-tunnel
tunnel_home=/home/${account}
if ! id "${account}" >/dev/null 2>&1; then
  useradd --create-home --home-dir "${tunnel_home}" --shell /bin/bash --comment "R640 reverse SSH tunnel" "${account}"
fi
tunnel_home=$(getent passwd "${account}" | cut -d: -f6)
install -d -m 700 -o "${account}" -g "$(id -gn "${account}")" "${tunnel_home}/.ssh"
auth_file=${tunnel_home}/.ssh/authorized_keys
if [[ ! -e ${auth_file} ]]; then
  install -m 600 -o "${account}" -g "$(id -gn "${account}")" /dev/null "${auth_file}"
fi

echo "Paste the R640 public key (one ssh-ed25519 line); never paste a private key."
IFS= read -r public_key || true
read -r key_type key_data key_comment <<<"${public_key}"
if [[ ${key_type} != ssh-ed25519 || ! ${key_data} =~ ^[A-Za-z0-9+/]+={0,2}$ ]]; then
  echo "Expected one valid ssh-ed25519 public-key line." >&2
  exit 1
fi

tmp_key=$(mktemp)
tmp_auth=$(mktemp "${tunnel_home}/.ssh/authorized_keys.XXXXXX")
trap 'rm -f "${tmp_key}" "${tmp_auth}"' EXIT
printf '%s\n' "${public_key}" >"${tmp_key}"
ssh-keygen -lf "${tmp_key}" >/dev/null || { echo "Invalid public key." >&2; exit 1; }

cp -p "${auth_file}" "${auth_file}.before-r640-tunnel.$(date +%Y%m%d%H%M%S)"
key_options='restrict,port-forwarding,permitlisten="127.0.0.1:22220",permitopen="127.0.0.1:22220",command="/usr/bin/false"'
found=false
while IFS= read -r line || [[ -n ${line} ]]; do
  if [[ " ${line} " == *" ${key_data} "* ]]; then
    if [[ ${line} == "${key_options} ${key_type} ${key_data}"* ]]; then
      printf '%s\n' "${line}" >>"${tmp_auth}"
    else
      printf '%s %s %s %s\n' "${key_options}" "${key_type}" "${key_data}" "${key_comment:-r640-reverse-tunnel}" >>"${tmp_auth}"
    fi
    found=true
  else
    printf '%s\n' "${line}" >>"${tmp_auth}"
  fi
done <"${auth_file}"
if [[ ${found} == false ]]; then
  printf '%s %s %s %s\n' "${key_options}" "${key_type}" "${key_data}" "${key_comment:-r640-reverse-tunnel}" >>"${tmp_auth}"
fi
chown "${account}:$(id -gn "${account}")" "${tmp_auth}"
chmod 600 "${tmp_auth}"
mv -f "${tmp_auth}" "${auth_file}"

command -v restorecon >/dev/null && restorecon -R "${tunnel_home}/.ssh" || true
echo
echo "Cloud tunnel account ready: ${account}"
echo "Only the loopback listener 127.0.0.1:22220 is permitted for this key."
echo "Direct TCP forwarding is also limited to that same loopback endpoint."
echo "The R640 service will reconnect automatically. On Windows, connect with: ssh rokid-r640"
