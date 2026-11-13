# 固件与分区

## 机型与校验

现场机型为纽曼 MF761W V1.1 OM QX，固件分支 P005。Linux 内核为 3.4.110-rt140，处理器标识为 TSP ZX297520V3。MTD4 为只读 SquashFS 根文件系统，起始偏移 `0x468000`，上限 3,244,032 字节。NOR 擦除块为 32,768 字节，共 99 块。MTD0 至 MTD3 包含启动、设备信息或厂商镜像，MTD5 为可写数据分区。

| 文件 | SHA-256 |
| --- | --- |
| 适用的原厂 MTD4 完整备份 | `b21b331da0a6376b18c1af97eb7785838e2695ad0ae85db474ca15cf8312c17f` |
| 现场实际安装的 3,186,688 字节镜像 | `082c94d2eda4015b9400dd5ef7341d7a2af4455d47c2fc2ce6d929760a5653d9` |
| 现场安装后含尾部填充的完整 MTD4 读回 | `1586e04e22591352941d8f8090fa8a08609c364a32d9111df1f38ceb2d9a8adf` |
| 公开版 MTD4 镜像 | `1ad68a9acd1f432e99636c128a895fcef4cd6d83ed734ad81967c843981e97dc` |
| 固件覆盖包 | `1ce98bbcefc8c12a4434f2d9be59cf0b07a43cdb51380e8434cbe73c9c2655be` |

## 现场处理过程

2026-10-12 在本机备份六个分区并核对读取长度和摘要。对匹配的原厂 MTD4 先执行无擦写演练，再从 RAM 运行刷写程序，按 99 个擦除块逐块写入、读回，最后写首块。软件重启后再次读出完整 MTD4，内容与候选镜像加 `0xFF` 尾部填充一致。启动、设备信息、引导及厂商 imagefs 分区保持原有校验值。掉电中断、硬件救援和故障回退分支未进行现场验证。

2026-11-13 的 USB 供网、转发与灯控通过运行时部署完成。现场只读镜像中自带的旧加密核心无法完成本次节点握手，Windows 改为将新版核心装入设备 RAM。已经具备现场基线的设备可直接使用运行时部署，不需要为了这次 USB 改造再次写闪存。[运行时部署](deployment.md)

## 公开版交付

公开的 `.squashfs` 是可直接检查的 MTD4 分区镜像。覆盖包在 `/usr/share/newman-usb/` 加入服务脚本、防火墙和设备程序，保留原厂内核接口，静态维护页位于 `/etc_ro/web/index.html`。代理服务由 Windows 通过 USB 装入并启动。

公开版锁定系统口令校验项，并清除默认管理口令、热点密钥、序列号、服务令牌和 PPP 预置凭据；移除出厂 APN 数据库、userdata 初始化模板和无线 MAC 默认配置。现场旧代理核心及其编译路径未放入此版本。已有热点配置来自设备自己的 MTD5；公开版在空白 userdata、恢复出厂、蜂窝联网以及实际开机时的行为未经验证。请保留本机私有分区备份。发布包没有整片闪存或 userdata 恢复文件。

公开版本连续两次构建得到相同 SHA-256，SquashFS 解包后的全部条目、内容、权限、所有者、时间和链接均经过回读比对。固件包处理程序只读取本地基线并输出镜像文件。公开镜像与原机镜像使用不同校验表，历史刷写器仅适配原机镜像。

## 本地复现

准备 Python 3.8 以上、squashfs-tools-ng 1.3.2，以及上表匹配的自有 MTD4 原厂备份。以下命令在解压后的项目根目录执行。

```powershell
python firmware/compose.py check
python firmware/compose.py compose --public-image --stock .local/mtd4-stock.bin --tools C:/tools/squashfs/bin --output .local/public.squashfs
Get-FileHash .local/public.squashfs -Algorithm SHA256
```

去掉 `--public-image` 会生成保留本机原厂默认配置的本地镜像。该变体应在本地保管。合成器校验原厂 SHA-256、输出尺寸、SquashFS 标识和完整回读，输出位置必须为新文件。仓库提供已测试的本地合成工具，公开镜像的刷写与冷启动尚待独立实机验证。[固件验证数据](../evidence/firmware.json)
