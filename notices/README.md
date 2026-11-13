# 软件来源

本仓库记录一台 MF761W 的设备改造。公开 MTD4 使用匹配的原厂 P005 用户空间，部分厂商二进制及无线固件沿用原有版权。发布包中的运行时脚本、设备工具源码、适配代码与现场测试记录位于各自目录。仓库未对厂商软件或第三方组件重新授权。

| 组件 | 用途与说明 |
| --- | --- |
| 厂商 P005 用户空间 | BusyBox 1.21.0 和厂商热点服务等来自原厂只读基线；公开构建移除了个人及工厂凭据。P005 内核与引导程序沿用设备已有分区。原厂精确对应源码未收录 |
| BearSSL 0.6 | 静态链接到 HTTPS 健康检查器。[许可文本](BearSSL.txt) |
| musl | 随 Zig 的 ARM Linux 工具链链接到三个辅助程序。[声明](musl.txt) |
| 公共 CA 信任锚 | 检查器内置的公钥与证书主体来自公共 CA 根集合。[证书集合说明](CA-bundle.txt) |
| vless-core-cli / OpenSSL | 核心由使用者从固定上游版本在本地构建。本文档列出上游引用与相应版权说明。[上游第三方说明](https://github.com/notfence/vless-core-cli/blob/v2.0.0/THIRD_PARTY_LICENSES.txt)、[OpenSSL 许可](https://github.com/openssl/openssl/blob/openssl-3.5.7/LICENSE.txt) |
| SquashFS 工具、Zig、Android Platform Tools | 使用者自行取得的构建或设备通信工具。[版本和来源](../docs/build.md) |
