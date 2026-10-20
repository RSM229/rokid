# R640 SSH 反向通道

此配置让 R640 主动连到公网云服务器，再从 Windows 经云服务器跳转登录。云服务器只监听 `127.0.0.1:22220`，不会把 R640 的 SSH 端口暴露到公网，也不改动现有 FRP。

## 三台机器各执行一次

先将 `docs/ssh-r640-tunnel` 里的脚本分别复制到对应机器。R640 当前无法从外网直连，因此需要通过 Xshell、U 盘或现有内网方式把 `setup-r640.sh` 放到 R640 上；`setup-cloud.sh` 同样复制到云服务器。

1. **本机 Windows PowerShell**：运行 `& ".\setup-local-ssh.ps1"`。默认云端登录密钥为 `$HOME\.ssh\id_ed25519`，R640 登录用户默认为 `baize`。用户名不同可运行 `& ".\setup-local-ssh.ps1" -R640User 你的用户名`。脚本生成专用的 `id_ed25519_r640`，并打印它的公钥；只复制公钥，不要复制私钥。
2. **物理机 R640**：运行 `sudo bash setup-r640.sh`。若登录用户名不是 `baize`，运行 `sudo bash setup-r640.sh 你的用户名`。按提示粘贴 Windows 公钥。脚本安装开机自启、断线重连的 systemd 服务，输出 R640 隧道公钥和主机指纹。
3. **公网云服务器**：运行 `sudo bash setup-cloud.sh`，粘贴 R640 输出的隧道公钥。脚本配置专用账号 `rokid-r640-tunnel`，并将这把密钥限制为指定的反向转发端口。
4. **回到 Windows**：运行 `ssh rokid-r640`。首次连接时核对 R640 输出的主机指纹后再确认。连接成功后就是 R640 的 shell。

Windows 使用现有 SSH 密钥登录云端跳板，另用新生成的专用密钥登录 R640。R640 通过内网出站 SSH 连云端，因此不要求 R640 有公网 IP 或开放入站端口。脚本不会复制、上传或输出任何私钥。

## 检查状态

R640：

```bash
sudo systemctl status r640-reverse-ssh-tunnel --no-pager
sudo journalctl -u r640-reverse-ssh-tunnel -n 30 --no-pager
```

云服务器应只看到回环地址监听：

```bash
ss -lnt | grep 22220
```

## 移除

R640 执行 `sudo systemctl disable --now r640-reverse-ssh-tunnel`，再删除 `/etc/systemd/system/r640-reverse-ssh-tunnel.service` 并执行 `sudo systemctl daemon-reload`。云端专用密钥和账号可单独清理。Windows 端删除 SSH 配置中 `BEGIN/END ROKID-R640-TUNNEL` 标记之间的段落即可。
