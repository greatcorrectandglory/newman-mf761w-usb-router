#!/bin/sh
# P005 access point and local DHCP. The Windows host owns the Internet data path.
set -eu
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
umask 077
base=/etc_rw/mf761w-host-ap
slot=${0%/*}
state=/tmp/mf761w-host-ap
usb=usblan0
mkdir -p "$state"

alive() {
    test -s "$1" || return 1
    pid=$(cat "$1")
    case "$pid" in ''|*[!0-9]*) return 1;; esac
    test -r "/proc/$pid/cmdline" && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -Fq "$2"
}
stop_owned() {
    if alive "$1" "$2"; then
        kill "$pid" 2>/dev/null || :
        for wait in 1 2 3 4 5; do
            alive "$1" "$2" || return 0
            sleep 1
        done
        ! alive "$1" "$2"
    fi
}
led() {
    case "$1" in green) on=wifi_led; other=modem_r_led;; red) on=modem_r_led; other=wifi_led;; *) return 1;; esac
    for n in "$other" modem_b_led; do
        d=/sys/class/leds/$n
        if ! grep -q '\[none\]' "$d/trigger" || test "$(cat "$d/brightness")" != 0; then
            echo none > "$d/trigger"; echo 0 > "$d/brightness"
        fi
    done
    d=/sys/class/leds/$on
    if ! grep -q '\[timer\]' "$d/trigger" || test "$(cat "$d/delay_on" 2>/dev/null)" != 500 || test "$(cat "$d/delay_off" 2>/dev/null)" != 500; then
        echo timer > "$d/trigger"; echo 500 > "$d/delay_on"; echo 500 > "$d/delay_off"
    fi
}
snapshot() {
    test ! -f "$state/saved" || return 0
    iptables-save > "$state/original.v4"
    ip6tables-save > "$state/original.v6"
    for nic in br0 "$usb"; do
        ip -o -4 addr show dev "$nic" | awk '{print $4}' > "$state/addr-$nic"
        ip -4 route show dev "$nic" | sed '/proto kernel/d' > "$state/routes-$nic"
    done
    test -s "$state/addr-br0" || return 1
    cat /sys/class/net/br0/address > "$state/br-mac"
    cat /sys/class/net/wlan0-vxd/flags > "$state/wlan-flags"
    cat /proc/sys/net/ipv4/ip_forward > "$state/ip4"
    cat /proc/sys/net/ipv6/conf/all/forwarding > "$state/ip6"
    if test -d "/sys/class/net/br0/brif/$usb"; then touch "$state/usb-bridged"; fi
    if alive /etc_rw/udhcpd.pid /etc_rw/udhcpd.conf; then touch "$state/vendor-dhcp"; fi
    for n in wifi_led modem_r_led modem_b_led; do
        d=/sys/class/leds/$n
        echo "$n $(sed 's/.*\[\([^]]*\)\].*/\1/' "$d/trigger") $(cat "$d/brightness") $(cat "$d/delay_on" 2>/dev/null || echo 0) $(cat "$d/delay_off" 2>/dev/null || echo 0)"
    done > "$state/leds"
    touch "$state/saved"
}
halt_workers() {
    stop_owned "$state/dhcp.pid" "$base/"
    stop_owned "$state/relay.pid" "$base/"
}
precheck() {
    test -d /sys/class/net/br0/brif/wlan0 && test -d "/sys/class/net/$usb"
    test -x "$slot/relay" && test -r "$slot/dhcp.conf"
    iptables-restore --test < "$slot/filter.v4"
    ip6tables-restore --test < "$slot/filter.v6"
    if alive /tmp/newman-usb/manager.pid /etc_rw/newman-usb/manager.sh ||
       alive /tmp/newman-pc-ap/relay.pid /etc_rw/newman-usb/usb-ethernet ||
       alive /tmp/newman-pc-ap/udhcpd.pid /etc_rw/newman-usb/ap-dhcp.conf; then
        echo 'Other Newman gateway active' >&2; return 3
    fi
}
ensure() {
    precheck
    snapshot
    previous=$(cat "$state/slot" 2>/dev/null || :)
    if test "$previous" != "$slot"; then halt_workers; fi
    stop_owned /etc_rw/udhcpd.pid /etc_rw/udhcpd.conf
    if test "$previous" != "$slot" || test -d "/sys/class/net/br0/brif/$usb" ||
        ! ip -4 addr show br0 | grep -q 'inet 192.168.77.2/' ||
        ! iptables -S INPUT | grep -q -- '--sport 68 --dport 67 -j ACCEPT' ||
        test "$(cat /proc/sys/net/ipv4/ip_forward)" != 0 ||
        test "$(cat /proc/sys/net/ipv6/conf/all/forwarding)" != 0 ||
        ! iptables -S FORWARD | grep -q '^-P FORWARD DROP' ||
        ! ip6tables -S FORWARD | grep -q '^-P FORWARD DROP'; then
        echo 0 > /proc/sys/net/ipv4/ip_forward
        echo 0 > /proc/sys/net/ipv6/conf/all/forwarding
        ip6tables-restore < "$slot/filter.v6"
        iptables-restore < "$slot/filter.v4"
        if test -d "/sys/class/net/br0/brif/$usb"; then brctl delif br0 "$usb"; fi
        ip -4 addr flush dev "$usb"
        ip link set wlan0-vxd down
        ifconfig br0 hw ether "$(cat "$state/br-mac")"
        ifconfig br0 192.168.77.2 netmask 255.255.255.0 up
    fi
    if ! alive "$state/dhcp.pid" "$slot/dhcp.conf"; then
        touch "$state/udhcpd.leases"
        (trap '' HUP; exec udhcpd -f "$slot/dhcp.conf") </dev/null >"$state/dhcp.log" 2>&1 &
        sleep 1
        alive "$state/dhcp.pid" "$slot/dhcp.conf" || return 4
    fi
    if ! alive "$state/relay.pid" "$slot/relay"; then
        (trap '' HUP; exec "$slot/relay") </dev/null >"$state/relay.log" 2>&1 & echo $! > "$state/relay.pid"
        sleep 1
        alive "$state/relay.pid" "$slot/relay" && grep -qi '0100007F:4DA2 .* 0A ' /proc/net/tcp || return 5
    fi
    echo "$slot" > "$state/slot.new"; mv "$state/slot.new" "$state/slot"
}
restore() {
    test -f "$state/saved" || return 0
    halt_workers
    if test -f "$state/usb-bridged" && ! test -d "/sys/class/net/br0/brif/$usb"; then brctl addif br0 "$usb"; fi
    ifconfig br0 hw ether "$(cat "$state/br-mac")"
    for nic in br0 "$usb"; do
        ip -4 addr flush dev "$nic"
        while read -r addr; do test -z "$addr" || ip addr add "$addr" dev "$nic"; done < "$state/addr-$nic"
        while read -r route; do
            test -z "$route" || ip -4 route replace $route dev "$nic"
        done < "$state/routes-$nic"
    done
    case "$(cat "$state/wlan-flags")" in *[13579bBdfDF]) ip link set wlan0-vxd up;; esac
    iptables-restore < "$state/original.v4"
    ip6tables-restore < "$state/original.v6"
    cat "$state/ip4" > /proc/sys/net/ipv4/ip_forward
    cat "$state/ip6" > /proc/sys/net/ipv6/conf/all/forwarding
    if test -f "$state/vendor-dhcp" && ! alive /etc_rw/udhcpd.pid /etc_rw/udhcpd.conf; then
        (trap '' HUP; exec udhcpd -f /etc_rw/udhcpd.conf) </dev/null >"$state/vendor.log" 2>&1 &
        for wait in 1 2 3 4 5; do
            sleep 1
            if alive /etc_rw/udhcpd.pid /etc_rw/udhcpd.conf; then break; fi
        done
        alive /etc_rw/udhcpd.pid /etc_rw/udhcpd.conf || return 6
    fi
    while read -r n trigger brightness delay_on delay_off; do
        d=/sys/class/leds/$n
        echo none > "$d/trigger"; echo "$brightness" > "$d/brightness"; echo "$trigger" > "$d/trigger"
        if test "$trigger" = timer; then
            for wait in 1 2 3; do test -f "$d/delay_on" && break; sleep 1; done
            echo "$delay_on" > "$d/delay_on"; echo "$delay_off" > "$d/delay_off"
        fi
    done < "$state/leds"
    rm -f "$state/saved" "$state/slot" "$state/usb-bridged" "$state/vendor-dhcp"
}
case "${1:-}" in
    check) precheck;;
    ensure) ensure;;
    restore) restore;;
    green|red) led "$1";;
    *) exit 2;;
esac
