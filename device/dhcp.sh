#!/bin/sh
set -e
test "$interface" = usblan0 || exit 1
state=/tmp/newman-usb
case "$1" in
    deconfig)
        ip route del default dev usblan0 2>/dev/null || :
        ifconfig usblan0 0.0.0.0 up
        rm -f "$state/lease"
        ;;
    bound|renew)
        set -- $router
        gw=${1:-}
        test -n "$ip" && test -n "$gw"
        case "$ip" in 192.168.0.*|127.*|0.*) exit 1;; esac
        case "$gw" in 192.168.0.*|127.*|0.*) exit 1;; esac
        ifconfig usblan0 "$ip" netmask "${subnet:-255.255.255.0}" up
        ip route replace default via "$gw" dev usblan0 metric 1
        if ip route get 192.168.0.2 | grep -q 'dev br0'; then
            for server in ${dns:-$gw}; do echo "nameserver $server"; done > "$state/resolv.new"
            if test "$(cat "$state/resolv.new")" != "$(cat /etc_rw/resolv.conf)"; then cat "$state/resolv.new" > /etc_rw/resolv.conf; fi
            echo "$ip $gw" > "$state/lease.new"
            mv "$state/lease.new" "$state/lease"
        else
            ip route del default dev usblan0 || :
            exit 1
        fi
        ;;
esac

