# Windows USB 供网与运行时部署

## 前提

使用具备原厂 P005 兼容分区、设备端管理权限及已授权 USB ADB 的 MF761W。现场运行时已在 2026-10-12 的精简根文件系统上验证。公开版根文件系统提供相同设备标记前缀和所需运行组件，整机启动验证范围见[固件记录](firmware.md)。电脑需要 Windows Internet 连接共享、Python 3.8 以上和 Android SDK Platform Tools。准备可用的四个 VLESS/REALITY TCP 节点。

从发布包解压到固定目录。在 PowerShell 中记录 ADB 路径及这台纽曼的实际序列号。与手机同时插入时，下面的 `--serial` 选择只影响目标设备。专用 ADB 端口为 15037。

```powershell
$env:MF761W_ADB = 'C:/tools/platform-tools/adb.exe'
$env:ANDROID_USER_HOME = Join-Path (Get-Location) '.local/adb-state'
New-Item -ItemType Directory -Force -Path $env:ANDROID_USER_HOME | Out-Null
& $env:MF761W_ADB -P 15037 devices -l
$env:MF761W_SERIAL = '<上一步显示的纽曼设备序列号>'
```

设备的 USB RNDIS 网卡标识为 `19d2:0581`。在 Windows 确认该网卡为共享的私有一侧，能访问互联网的实际网卡为公共一侧。电脑端的 ICS 网关在实测中为 `192.168.137.1`，热点网关为 `192.168.0.1`，两个网段应不同。

## 节点文件

在项目根目录创建 `.local/nodes/`，里面建立名称准确的 `US3`、`US2`、`TEST1`、`SPEED` 四个文件。每个文件只包含一条自己的完整 VLESS URI，纯 ASCII，不含行尾换行、备注片段或 BOM。使用 TCP 或 raw 传输、REALITY、有效 SNI 和公钥，支持 Vision。四个文件在设备上按 0600 保存。准备好的 ARM 核心放到 `.local/core-arm`。[核心构建步骤](build.md)

下例只展示字段结构，尖括号中的文本要替换为本人节点参数。

```text
vless://<UUID>@<HOST>:<PORT>?security=reality&sni=<SERVER_NAME>&pbk=<PUBLIC_KEY>&sid=<SHORT_ID>&fp=chrome&flow=xtls-rprx-vision&type=tcp
```

## 部署与启动

先在普通 PowerShell 校验本地核心与四条配置，然后在管理员 PowerShell 开启 Windows 共享。共享脚本识别唯一的在线物理上游以及 MF761W USB 网卡，在 `.local/` 记录变更前的共享状态；已有其他共享上游时会报告冲突。

```powershell
python host/router.py validate
& ./host/sharing.ps1
Get-Content .local/windows-sharing-result.json
```

在设置了 `MF761W_ADB` 和 `MF761W_SERIAL` 的普通 PowerShell 执行安装。程序会停止已有的本项目服务，上传并回读校验设备程序，写入四条本地配置，验证防火墙，然后启动设备。安装失败后可在排除原因后重试。

```powershell
python host/router.py install
python host/router.py status
```

`online US3 https` 表示 US3 的 HTTPS 探测通过。`online US3 traffic` 表示本轮内确实有新的加密流量交付给客户端。设备用绿色 Wi-Fi 灯 `wifi_led` 和红色蜂窝灯 `modem_r_led` 表示状态。USB 中断、切换或无法确认连通时红闪。断电时指示灯熄灭。

电脑重新登录、设备断电或重插后，需要 `start` 或持续运行的 `watch` 通过 USB 重新加载 RAM 核心。运行以下命令可在当前终端持续监听重插。需要登录后自动启动时，可将同一命令加入自己的 Windows 登录启动项，并将 ADB 路径与序列号作为参数传给它。固定 Python 和脚本的绝对路径，工作目录设置为本项目。

```powershell
python host/router.py watch --adb $env:MF761W_ADB --serial $env:MF761W_SERIAL
```

| 操作 | 命令 |
| --- | --- |
| 查看状态与转发计数 | `python host/router.py status` |
| 停止加密转发与自动激活 | `python host/router.py stop` |
| 恢复自动激活并启动 | `python host/router.py start` |
| 恢复本次设备接管前保存的网络状态 | `python host/router.py restore` |
| 恢复本次电脑共享变更 | 同一份安装目录下，在管理员 PowerShell 执行 `./host/sharing.ps1 -Restore` |

设备网络快照位于 RAM，覆盖当前启动周期。恢复网络配置不会回刷 MTD4。手机验收时可暂时关闭移动数据，连接设备已有热点，用 Chrome 访问百度、Google 和 example.com，并核对 Android 的 Wi-Fi 联网标志。
