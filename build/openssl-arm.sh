#!/bin/sh
set -eu
: "${ZIG:?Set ZIG to the absolute Zig executable path}"
: "${MAKE:=make}"
: "${SHELL_EXE:=/bin/sh}"
# Run in the unpacked OpenSSL 3.5.7 source directory.

export CC='"'"$ZIG"'" cc -target arm-linux-musleabi -mcpu=arm1176jz_s -mfloat-abi=soft -Oz -ffunction-sections -fdata-sections'
export AR='"'"$ZIG"'" ar'
export RANLIB='"'"$ZIG"'" ranlib'
perl Configure linux-generic32 no-shared no-tests no-apps no-docs no-asm no-module no-dso no-engine no-legacy no-ui-console no-comp no-sctp no-ssl3 no-tls1 no-tls1_1 no-dtls no-dsa no-dh no-srp no-camellia no-aria no-bf no-cast no-des no-idea no-md2 no-md4 no-mdc2 no-rc2 no-rc4 no-rc5 no-ripemd no-seed no-sm2 no-sm3 no-sm4 no-whirlpool --prefix=/opt/vless-arm -Oz -ffunction-sections -fdata-sections
"$MAKE" SHELL="$SHELL_EXE" -j4 build_libs
