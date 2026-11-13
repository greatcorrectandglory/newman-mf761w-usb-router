#!/bin/sh
set -eu
umask 077
root=/etc_rw/newman-usb
state=/tmp/newman-usb
case "$1" in US3|US2|TEST1|SPEED) name=$1;; *) exit 2;; esac
uri=$(cat "$root/nodes/$name")
authority=${uri#*@}
hostport=${authority%%\?*}
host=${hostport%:*}
port=${hostport##*:}
case "$host" in *[!0-9.]*)
    line=$(ping -c 1 -W 1 "$host" 2>/dev/null | head -1)
    address=$(echo "$line" | sed -n 's/^PING .* (\([^)]*\)).*/\1/p')
    ;;
    *) address=$host;;
esac
case "$address" in ''|*[!0-9.]*) exit 3;; esac
echo "$address" > "$state/active-endpoint"
echo -n "${uri%%@*}@$address:$port?${authority#*\?}" > "$state/active.new"
mv "$state/active.new" "$state/active.uri"
