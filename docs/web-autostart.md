# RK3588 Web 后端开机自启

Web 控制台后端使用 `systemd` 管理，板子开机后会自动启动 FastAPI 服务。如果进程异常退出，`systemd` 会自动重启。

## 文件

```text
deploy/rk3588-web.service
deploy/rk3588-web.env.example
deploy/run_web.sh
deploy/install_web_service.sh
```

## 一键安装

在板子上执行：

```bash
cd /opt/rk3588-camera/current
sudo ./deploy/install_web_service.sh
```

安装脚本会完成：

1. 创建/复用 `rkcamera` 系统用户。
2. 创建 `/etc/rk3588-camera/web.env`。
3. 创建 `/etc/rk3588-camera/web.json`，用于配置真实检测命令。
4. 创建 `/opt/rk3588-camera/current/.venv` 并安装 FastAPI/Uvicorn。
5. 安装并启用 `rk3588-web.service`。
6. 立即启动 Web 后端。

## 访问

```text
http://板子IP:8000
```

端口在 `/etc/rk3588-camera/web.env` 里配置：

```bash
WEB_HOST=0.0.0.0
WEB_PORT=8000
RK3588_WEB_CONFIG=/etc/rk3588-camera/web.json
```

## 常用命令

查看状态：

```bash
sudo systemctl status rk3588-web --no-pager
```

实时看日志：

```bash
sudo journalctl -u rk3588-web -f
```

重启：

```bash
sudo systemctl restart rk3588-web
```

停止：

```bash
sudo systemctl stop rk3588-web
```

关闭开机自启：

```bash
sudo systemctl disable --now rk3588-web
```

重新开启开机自启：

```bash
sudo systemctl enable --now rk3588-web
```

## 真实检测命令配置

后端会读取：

```text
/etc/rk3588-camera/web.json
```

默认内容来自：

```text
apps/web/backend/config.example.json
```

其中 `pipeline_command` 是点击网页“启动检测”时真正执行的命令。最后一个参数建议保持 `1`，表示让 `mpp_rga_thread_pool` 输出 JSON 检测事件，浏览器才能实时收到检测框。
