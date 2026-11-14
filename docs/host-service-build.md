# Windows 热点服务构建

扩展源码包含 Windows 用户态网关、ARM USB 帧中继、设备 DHCP 配置和 Windows 用户服务。发布包使用文件清单打包，排除工作区配置、运行日志、设备标识、节点文件和固件分区。ADB、Xray 和 Python 由安装者从各自发行渠道取得。

## 构建工具

验证环境使用 Windows x64、Go 1.27.4、Zig 0.14.1 和 Python 3.12。Go 的直接和间接依赖固定在 `extension/gateway/go.mod`、`go.sum`。Xray 用于安装后的运行，Windows x64 26.11.9 官方压缩包 SHA-256 为 `1262522266337b824f07beb8e1375786b437683b3890105469088f8e17e0a69d`。[Go 下载](https://go.dev/dl/) · [Zig 下载](https://ziglang.org/download/) · [Xray 发行记录](https://github.com/XTLS/Xray-core/releases/tag/v26.11.9)

在仓库根目录执行以下命令。构建程序会验证 Go 模块、运行网关测试和静态检查、交叉编译软浮点 ARM 中继、检查两个二进制文件的目标架构，并验证 ZIP 读回内容。每个 ZIP 条目使用固定日期和权限；使用相同工具链可比对两次生成的摘要。

```powershell
py -3.12 -m unittest discover -s extension/tests -v
py -3.12 build/extension.py --go C:\Tools\Go\bin\go.exe --zig C:\Tools\Zig\zig.exe --output .local/release-extension
Get-Content .local/release-extension/SHA256SUMS.txt
```

## 运行边界

Windows 计划任务使用登录用户身份。Python 监督设备身份、独立的 USB ADB 服务、网关进程和本地 Xray。Windows Job Object 在监督进程退出时关闭其工作进程，随后计划任务重新启动服务。节点链接仅在安装时从用户文件读入，使用 Windows DPAPI 加密存储，并通过标准输入交给 Xray。进程参数和公开故障证据只含节点角色。

设备安装目录使用内容摘要作为版本名，切换前校验独立的暂存文件。网络快照、设备 DHCP 租约和状态保存在该次启动的 RAM 内，软件包仅写入该扩展专属的数据目录。Windows 安装目录保留当前及上一个版本所需的文件，支持重新安装、升级中断后的恢复和卸载。

## 第三方组件

Go、tun2socks、gVisor、btree、Go 扩展模块和 musl 的许可文本随扩展附带。tun2socks 的网卡抽象与 gVisor 用户态网络栈用于 Windows TCP 转发。ARM 中继静态链接 musl。ADB、Xray、厂商固件与个人节点配置由各自的发行和配置渠道提供。[许可文件](../extension/notices/) · [gVisor 网络架构](https://gvisor.dev/docs/architecture_guide/networking/)
