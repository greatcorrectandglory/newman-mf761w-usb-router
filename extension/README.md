# 纽曼 MF761W 热点服务

这款扩展把一台 Windows 电脑变成 MF761W 热点的网关。手机和其他 Wi-Fi 客户端加入纽曼已有的热点即可上网，地址由纽曼分配，电脑完成 DNS、IPv4 TCP 转发及节点加密。电脑服务临时中断时，已经启动的纽曼仍可分配地址，公网访问在电脑恢复后继续。

## 安装前准备

- **设备**为 MF761W V1.1 OM QX（P005），已运行本项目支持的精简 Linux 基线，具备已授权的 USB ADB 管理权限。安装程序读取型号标记，并只操作指定设备的扩展目录。相同商品名可能对应不同硬件。
- **电脑**使用 Windows 10 或 11 x64，安装可用的 64 位 Python 3.8 或更高版本。用日常登录的 Windows 账户安装和运行。服务随用户登录启动。电脑需要保持开机、联网、登录，并通过 USB 连接纽曼。
- **两个上游工具**需要解压到电脑上的固定目录。安装向导会询问 `adb.exe` 和 `xray.exe` 的路径。本次验证使用 Platform Tools 37.0.1 和 Xray 26.11.9 Windows x64。[Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) · [Xray Windows x64 发行版](https://github.com/XTLS/Xray-core/releases/tag/v26.11.9)
- **一个本地 UTF-8 文本文件**，每行填写一条本人有权使用的 VLESS/REALITY TCP 链接。第一行为主节点，随后可增加最多三个备用节点。支持 `tcp`、`raw` 和可选的 Vision。行末换行、UTF-8 BOM、链接备注均可保留。安装会使用当前账户的 Windows DPAPI 保存配置；原始文本文件仍由使用者保管。
- 已使用旧版设备加密模式时，先用**原安装目录及其控制工具**停止旧版并恢复接管前的网络配置，再安装扩展。若显示其他网关正在运行，安装会报告冲突。扩展沿用热点的 SSID 和无线密码。

## 开始使用

1. 从 GitHub Release 下载 `newman-hotspot-windows-x64-v1.2.0.zip` 和 `SHA256SUMS.txt`。使用 `Get-FileHash -Algorithm SHA256 .\newman-hotspot-windows-x64-v1.2.0.zip` 核对摘要，随后解压 ZIP。
2. 双击解压目录内的 **Start-Here.cmd**，按提示输入两个程序及节点文本文件的路径。Windows 如将从互联网下载的脚本标记为受限，可在已核对摘要的 ZIP 文件属性中选择“解除锁定”，然后重新解压。
3. 用手机连接纽曼热点。客户端取得 `192.168.77.100–249` 范围内的地址，默认网关和 DNS 均为 `192.168.77.1`。纽曼设备与其 DHCP 服务使用 `192.168.77.2`。
4. 用下方的 `Status` 查看 `online` 和节点角色，再从手机打开实际网页。绿灯闪烁表示最近一次经过节点的 HTTPS 检查成功，红灯闪烁表示正在等待或切换。电脑突然断电后指示灯可能保留最后一次设置。

`Doctor` 会读取无线配置中的客户端数量上限。本次实测设备的 `max_num_sta` 为 2，表示最多允许两台 Wi-Fi 设备同时关联。DHCP 地址池用于分配和续租，不能据此计算无线并发容量。

也可用 PowerShell 安装。将示例路径替换为实际位置。若多台设备同时接入，可增加 `-Serial` 指定纽曼的 USB 序列号。

```powershell
.\Hotspot.ps1 -Action Install -Adb C:\Tools\platform-tools\adb.exe -Xray C:\Tools\Xray\xray.exe -NodesFile C:\Private\hotspot-nodes.txt
```

## 管理与故障恢复

日常控制入口位于当前用户的安装目录。启动、停止和状态命令可重复执行。

```powershell
$hotspot = Join-Path $env:LOCALAPPDATA 'NewmanHotspot\service.ps1'
& $hotspot -Action Status
& $hotspot -Action Doctor
& $hotspot -Action Stop
& $hotspot -Action Start
```

| 状态 | 现场检查 |
| --- | --- |
| `waiting-usb` | 确认纽曼有供电、数据线正常、ADB 已授权。专用 ADB 服务使用 15037 端口，使用支持 libusb 的 Windows Platform Tools。 |
| `upstream-failed` | 手机仍能取得本地地址。检查电脑上网状态和所选节点。连续两次 HTTPS 检查失败后按顺序切换，USB 中断期间保留已选节点。 |
| `waiting-supervisor` | 查看当前 Windows 用户的“MF761W Hotspot Service”计划任务。任务在登录时启动，并在登录期间每分钟检查；工作进程退出后由监控重新拉起。 |
| 手机显示“IP 配置失败” | 运行 `Doctor` 核对设备身份和纽曼本机 DHCP；重新连接热点，检查手机是否自动获取地址。 |
| `online`，某一站点仍很慢 | 分别比较首次显示与整页加载，核对具体站点和静态资源。主页可打开与全部资源快速加载是两项独立指标。 |

`Stop` 暂停电脑转发并保留纽曼 DHCP。使用新版本**解压目录**中的脚本升级。正常升级沿用设备选择、工具路径和本地节点。增加 `-NodesFile` 可替换节点，增加 `-Xray` 或 `-Adb` 可更新工具路径。调整节点前可保存原始私有节点文件。不同版本之间会保留上一个安装版本及其配置。

```powershell
.\Hotspot.ps1 -Action Upgrade
& $hotspot -Action Rollback
& $hotspot -Action Uninstall
```

回退和卸载需要连接原设备。卸载恢复本次接管前记录的网络配置、DHCP 和灯控，移除电脑端计划任务与服务文件。设备重启后服务会在该次启动重新记录基线。管理操作中断时，下次管理命令会尝试恢复上一次配置。专用 ADB 工具和用户提供的 Xray、原始节点文件继续由用户管理。

## 范围与数据

- 支持网页、TCP 应用和 DNS。公共 IPv6、一般 UDP 和 QUIC 不在当前支持范围。设备端关闭原生 IP 转发。热点流量通过 USB 到电脑，经本地 Xray 的加密节点出网。无需开启 Windows Internet 连接共享，也无需给手机设置系统代理。
- DNS 默认经节点向 `8.8.8.8` 使用 TCP 查询。连通性检查经节点访问 Google 的 204 检测地址，必要时尝试 example.com。检查校验证书。运行时仅记录节点角色、状态变化和汇总计数；配置文件及日志位于当前账户的 `%LOCALAPPDATA%\NewmanHotspot`。
- 已验证的硬件基线、浏览器测量及版本验收记录见项目说明。通过验证的设备数量、运行时间与并发条件决定适用范围。[公开仓库](https://github.com/greatcorrectandglory/newman-mf761w-usb-router)
