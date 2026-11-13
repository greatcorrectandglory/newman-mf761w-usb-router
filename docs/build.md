# ARM 构建与来源

发布包中的 `forward`、`zram`、`check` 是现场运行的 ARM 二进制。本次从公开仓库中的 C 源码重新编译三个程序，均与现场二进制逐字节一致。节点、Windows ADB 身份和核心均放在发布包之外的本地目录。公开的 MTD4 合成由 [firmware/compose.py](../firmware/compose.py) 完成。

## 依赖

| 组件 | 本次版本 | 来源 |
| --- | --- | --- |
| vless-core-cli | v2.0.0，提交 `0e1ba5cf7a9a9cf28fd7fe143f56e82b6a346632` | [上游标记](https://github.com/notfence/vless-core-cli/tree/v2.0.0) |
| OpenSSL | 3.5.7，源码归档 SHA-256 `a8c0d28a529ca480f9f36cf5792e2cd21984552a3c8e4aa11a24aa31aeac98e8` | [OpenSSL 发行版](https://github.com/openssl/openssl/releases/tag/openssl-3.5.7) |
| Zig | 0.14.1，Windows x86_64 ZIP SHA-256 `554f5378228923ffd558eac35e21af020c73789d87afeabf4bfd16f2e6feed2c` | [Zig 下载](https://ziglang.org/download/) |
| BearSSL | 0.6 | [发布源码](https://bearssl.org/bearssl-0.6.tar.gz) |
| squashfs-tools-ng | 1.3.2，Windows ZIP SHA-256 `891b1ed46dd856d05e429b01de2c7d86175a4c22478b5a6770f94b9c2100d0f6` | [1.3.2 工具包](https://infraroot.at/pub/squashfs/windows/squashfs-tools-ng-1.3.2-mingw64.zip) |
| Android SDK Platform Tools | 现场为 37.0.1 | [Android 官方下载](https://developer.android.com/tools/releases/platform-tools) |

## 运行核心

在本机取得上游源码，使用自己的副本。上游 v2.0.0 的 README 将其第三方许可证范围限定为列出的组件，发布包只提供适配与构建脚本。现场代理与此次公开脚本构建的目标均为 ARM Linux EABI、小端、软浮点，CPU 参数 `arm1176jz_s`。本次从干净的 v2.0.0 源码和已建立的 ARM OpenSSL 静态库成功构建公开脚本的输出，产物通过 ARM ELF 头校验。路径等编译输入会影响输出摘要，新编译的核心未经单独真机替换测试。

1. 解压 OpenSSL 和 Zig。在支持 Perl/Make 的 shell 中进入 OpenSSL 源码目录，设置 `ZIG` 为编译器绝对路径，执行项目的 `build/openssl-arm.sh`。Windows Git Bash 环境可设置 `MAKE=mingw32-make` 和 `SHELL_EXE` 为 Git Bash 的 sh 路径。构建结果应含 ARM 版 `libssl.a`、`libcrypto.a` 和配置后的头文件。
2. 获取固定版本上游源码。构建程序会复制源码到临时工作目录，适配 20 个核心会话槽、256 KiB 客户线程栈、一个 128 KiB 解析线程，以及精简 OpenSSL 的 DH 编译选项。

```powershell
git clone --depth 1 --branch v2.0.0 https://github.com/notfence/vless-core-cli .local/upstream
git -C .local/upstream rev-parse HEAD
python build/core.py --source .local/upstream --openssl C:/tools/openssl-3.5.7 --zig C:/tools/zig/zig.exe --output .local/core-arm
```

## 转发、zram 与健康检查

在项目根目录构建。下例为 PowerShell，`$bear` 指向已为同一 ARM 目标编译出 `build/libbearssl.a` 的 BearSSL 0.6 源码目录。BearSSL 自带 make 配置支持覆盖 `CC`、`LD`、`AR` 和 `CFLAGS`，需使用相同的 ARM 交叉编译器。

```powershell
$zig = 'C:/tools/zig/zig.exe'
$bear = 'C:/tools/bearssl-0.6'
$flags = @('cc','-target','arm-linux-musleabi','-mcpu=arm1176jz_s','-mfloat-abi=soft','-Oz','-fno-sanitize=all','-ffunction-sections','-fdata-sections','-static','-s','-Wl,--gc-sections')
& $zig @flags device/src/forward.c -o .local/forward
& $zig @flags device/src/zram.c -o .local/zram
& $zig @flags "-I$bear/inc" device/src/check.c "$bear/build/libbearssl.a" -o .local/check
```

`device/src/anchors.h` 是现场健康检查器使用的公开 CA 信任锚。需要更新时使用 `python build/anchors.py <CA-bundle.pem> device/src/anchors.h`，Python 环境需有 `cryptography`。变更信任锚后需要重新编译并验证 `check`。压缩内存工具只启用已由设备初始化并具有有效交换签名的 zram 设备。
