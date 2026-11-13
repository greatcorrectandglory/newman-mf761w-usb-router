#!/bin/sh
set -e
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
umask 077
root=/etc_rw/newman-usb
state=/tmp/newman-usb
base=/tmp/newman-net-original
led() {
    case "$1" in green) on=wifi_led; off=modem_r_led;; red) on=modem_r_led; off=wifi_led;; *) return 2;; esac
    for n in "$off" modem_b_led; do
        l=/sys/class/leds/$n
        if ! grep -q '\[none\]' "$l/trigger" || test "$(cat "$l/brightness")" != 0; then
            echo none > "$l/trigger"; echo 0 > "$l/brightness"
        fi
    done
    l=/sys/class/leds/$on
    if ! grep -q '\[timer\]' "$l/trigger" || test "$(cat "$l/delay_on" 2>/dev/null)" != 500 || test "$(cat "$l/delay_off" 2>/dev/null)" != 500; then
        echo none > "$l/trigger"; echo 1 > "$l/brightness"; echo timer > "$l/trigger"
        echo 500 > "$l/delay_on"; echo 500 > "$l/delay_off"
    fi
}
snapshot() {
    mkdir -p "$base"
    if ! test -s "$base/firewall.v4"; then
        iptables-save > "$base/firewall.v4"; ip6tables-save > "$base/firewall.v6"
        cat /sys/class/net/br0/address > "$base/bridge-mac"
        cat /proc/sys/net/ipv4/ip_forward > "$base/forwarding"
        cat /proc/sys/net/ipv6/conf/all/forwarding > "$base/forwarding6"
        for l in /sys/class/leds/*; do
            t=$(sed 's/.*\[\([^]]*\)\].*/\1/' "$l/trigger")
            echo "${l##*/} $t $(cat "$l/brightness") $(cat "$l/delay_on" 2>/dev/null || echo 0) $(cat "$l/delay_off" 2>/dev/null || echo 0)"
        done > "$base/leds"
    fi
    if ! test -f "$base/resolv"; then cp /etc_rw/resolv.conf "$base/resolv"; fi
    if ! test -s "$base/minfree"; then cat /proc/sys/vm/min_free_kbytes > "$base/minfree"; fi
    if ! test -f "$base/swap-before"; then cat /proc/swaps > "$base/swap-before"; fi
}
dhcp_alive() {
    test -s "$state/dhcp.pid" || return 1
    pid=$(cat "$state/dhcp.pid")
    case "$pid" in ''|*[!0-9]*) return 1;; esac
    test -r "/proc/$pid/cmdline" && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -q 'udhcpc.*-i usblan0'
}
case "$1" in
    prepare)
        mkdir -p "$state"; snapshot
        "$root/zram" >/dev/null
        echo 1024 > /proc/sys/vm/min_free_kbytes
        iptables-restore --test < "$root/firewall.v4"; ip6tables-restore --test < "$root/firewall.v6"
        echo 0 > /proc/sys/net/ipv4/ip_forward
        echo 0 > /proc/sys/net/ipv6/conf/all/forwarding
        ip6tables-restore < "$root/firewall.v6"; iptables-restore < "$root/firewall.v4"
        if test -d /sys/class/net/br0/brif/usblan0; then brctl delif br0 usblan0; fi
        ifconfig br0 hw ether "$(cat "$base/bridge-mac")"
        ifconfig wlan0-vxd down || :
        ifconfig usblan0 up
        if ! dhcp_alive; then
            udhcpc -b -i usblan0 -t 2 -T 1 -A 5 -p "$state/dhcp.pid" -s "$root/dhcp.sh" >> "$state/dhcp.log" 2>&1 </dev/null
        fi
        # The factory startup can remove the USB default route after DHCP completes.
        if test -s "$state/lease"; then
            read leased gateway < "$state/lease"
            if ip -4 addr show dev usblan0 | grep -Fq "inet $leased/"; then
                ip route replace default via "$gateway" dev usblan0 metric 1
            elif dhcp_alive; then
                kill -USR1 "$pid" 2>/dev/null || :
            fi
        fi
        ;;
    red|green) led "$1";;
    restore)
        if dhcp_alive; then kill "$pid"; fi
        ip route del default dev usblan0 2>/dev/null || :
        ifconfig usblan0 0.0.0.0 up
        if ! test -d /sys/class/net/br0/brif/usblan0; then brctl addif br0 usblan0; fi
        ifconfig wlan0-vxd up
        iptables-restore < "$base/firewall.v4"; ip6tables-restore < "$base/firewall.v6"
        cat "$base/forwarding" > /proc/sys/net/ipv4/ip_forward
        cat "$base/forwarding6" > /proc/sys/net/ipv6/conf/all/forwarding
        cp "$base/resolv" /etc_rw/resolv.conf
        if ! grep -q '^/dev/zram0[[:space:]]' "$base/swap-before"; then "$root/zram" off; fi
        cat "$base/minfree" > /proc/sys/vm/min_free_kbytes
        while read n t b a f; do
            l=/sys/class/leds/$n
            echo none > "$l/trigger"; echo "$b" > "$l/brightness"; echo "$t" > "$l/trigger"
            if test "$t" = timer; then echo "$a" > "$l/delay_on"; echo "$f" > "$l/delay_off"; fi
        done < "$base/leds"
        ;;
    *) exit 2;;
esac
