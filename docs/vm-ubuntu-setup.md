# VMware Ubuntu 环境记录

本文档记录用于 RKNN/YOLOv5 量化环境的 Ubuntu 20.04 虚拟机配置。

## SSH

- 主机：`root@192.168.113.134`
- 私钥：`E:\deskTop\develop\项目\基于RK3588的智能摄像头\yolov5_stream\.agent-keys\rk3588_vm_root_ed25519`
- 公钥注释：`codex-rk3588-vm-root`

连接命令：

```powershell
& 'D:\Program Files\Git\usr\bin\ssh.exe' -i '.agent-keys\rk3588_vm_root_ed25519' root@192.168.113.134
```

## 静态 IP

Ubuntu 使用 VMware NAT 网络，当前通过 NetworkManager 固定 IP：

- 网卡：`ens33`
- 连接 UUID：`95c17853-94cf-347e-bfd6-4ef23c588d2e`
- IP：`192.168.113.134/24`
- 网关：`192.168.113.2`
- DNS：`192.168.113.2,8.8.8.8`

配置命令：

```bash
nmcli connection modify 95c17853-94cf-347e-bfd6-4ef23c588d2e \
  ipv4.method manual \
  ipv4.addresses 192.168.113.134/24 \
  ipv4.gateway 192.168.113.2 \
  ipv4.dns 192.168.113.2,8.8.8.8 \
  connection.autoconnect yes
nmcli connection up 95c17853-94cf-347e-bfd6-4ef23c588d2e
```

验证命令：

```bash
ip -br addr show ens33
ip route
getent hosts github.com
ping -c 2 192.168.113.2
ping -c 2 8.8.8.8
wget -S --spider -T 8 https://github.com
```

## VMware 共享目录

VMware 共享名：`yolov5_stream`

Ubuntu 挂载点：

```text
/mnt/hgfs/yolov5_stream
```

持久化方式：使用 systemd 服务，不使用 `/etc/fstab`，避免 FUSE 选项导致开机卡住。

服务文件：

```text
/etc/systemd/system/vmware-hgfs-mnt.service
```

服务内容：

```ini
[Unit]
Description=Mount VMware shared folders at /mnt/hgfs
After=vmtoolsd.service network-online.target
Wants=vmtoolsd.service network-online.target
ConditionPathExists=/usr/bin/vmhgfs-fuse

[Service]
Type=oneshot
ExecStartPre=/bin/mkdir -p /mnt/hgfs
ExecStartPre=-/bin/fusermount -u /mnt/hgfs
ExecStart=/usr/bin/vmhgfs-fuse .host:/ /mnt/hgfs -o allow_other
ExecStop=-/bin/fusermount -u /mnt/hgfs
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
```

启用和验证：

```bash
systemctl daemon-reload
systemctl enable vmware-hgfs-mnt.service
systemctl restart vmware-hgfs-mnt.service
systemctl status vmware-hgfs-mnt.service
findmnt /mnt/hgfs
ls -la /mnt/hgfs/yolov5_stream
```

## 恢复 DHCP

如果 VMware NAT 网段变化，先恢复 DHCP：

```bash
nmcli connection modify 95c17853-94cf-347e-bfd6-4ef23c588d2e \
  ipv4.method auto \
  ipv4.addresses "" \
  ipv4.gateway "" \
  ipv4.dns ""
nmcli connection up 95c17853-94cf-347e-bfd6-4ef23c588d2e
```

然后重新查看 IP：

```bash
ip -br addr show ens33
ip route
```
