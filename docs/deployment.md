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

在项目根目录创建 `.local/nodes/`，为自己的四个节点各准备一个文件。文件名表示配置的用途。

| 本地默认文件名 | 用途 |
| --- | --- |
| `primary` | 启动时优先使用的主节点 |
| `backup-1` | 第一个备用节点 |
| `backup-2` | 第二个备用节点 |
| `backup-3` | 第三个备用节点 |

每个文件保存一条完整的 VLESS URI，使用 ASCII 编码，省略行尾换行、备注片段和 BOM。使用 TCP 或 raw 传输、REALITY、有效 SNI 和公钥，支持 Vision。设备上的配置文件权限为 0600。准备好的 ARM 核心放到 `.local/core-arm`。[核心构建步骤](build.md)

已有四个配置文件时可以保留自己的文件名。在 `validate` 和 `install` 两条命令后都加上 `--profile-files`，依次传入主节点和三个备用节点的本地文件名。主机按该顺序安装，设备状态始终使用上表的角色。由 v1.0.0 升级时，指定原有的四个文件名或按上表命名，再执行安装即可。

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

`online primary https` 表示主节点通过 HTTPS 证书和响应码检查。`online primary traffic` 表示已有连接在本轮探测期间将新的下行数据交付给热点客户端，最近一次交付在 12 秒内。绿灯为 `wifi_led`，红灯为 `modem_r_led`。USB 中断、节点切换或无法确认连通时红闪。断电时指示灯熄灭。

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
