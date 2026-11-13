#!/bin/sh
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
umask 077
root=/etc_rw/newman-usb
state=/tmp/newman-usb
core=
forward=
checkpid=
sleeper=
stop_child() {
    test -n "$1" || return 0
    kill "$1" 2>/dev/null || :
    wait "$1" 2>/dev/null || :
}
clear_forward() {
    if test -n "$forward"; then stop_child "$forward"; forward=; fi
}
finish() {
    trap - TERM INT EXIT
    stop_child "$sleeper"; stop_child "$checkpid"
    clear_forward; stop_child "$core"
    "$root/network.sh" red
    echo 'offline - stopped' > "$state/status"
    exit 0
}
trap '' HUP
trap finish TERM INT
trap 'finish' EXIT
wait_seconds() {
    sleep "$1" & sleeper=$!
    wait "$sleeper"; sleeper=
}
set_status() {
    phase="$1"; detail="$2"
    if test "$phase" = online; then
        "$root/network.sh" green
    else
        if test "$detail" != probe || test "$failed" -gt 0; then clear_forward; fi
        "$root/network.sh" red
    fi
    echo "$phase $name $detail" > "$state/status.new" && mv "$state/status.new" "$state/status"
    event="$phase $name $detail"
    if test "$event" != "$previous"; then
        echo "$(date -u +%s) $event" >> "$state/events"
        previous="$event"
        if test "$(wc -c < "$state/events")" -gt 16384; then mv "$state/events" "$state/events.previous"; fi
    fi
}
wan_present() {
    test ! -d /sys/class/net/br0/brif/usblan0 &&
    ip -4 route | grep -q '^default .*dev usblan0' &&
    test "$(cat /sys/class/net/usblan0/carrier 2>/dev/null)" = 1
}
probe() {
    started=$(date -u +%s)
    evidence=https
    for target in google example; do
        "$root/check" "$target" > "$state/probe.log" 2>&1 & checkpid=$!
        wait "$checkpid"; result=$?; checkpid=
        if test "$result" = 0; then return 0; fi
        wan_present || return 1
    done
    if test -n "$forward" && kill -0 "$forward" 2>/dev/null; then
        received=$(stat -c %Y "$state/last-proxy-data" 2>/dev/null) || return 1
        now=$(date -u +%s)
        if test "$received" -gt "$started" && test "$received" -le "$now" &&
           test "$(expr "$now" - "$received")" -le 12; then
            evidence=traffic
            return 0
        fi
    fi
    return 1
}
start_core() {
    stop_child "$core"; core=
    rm -f "$state/last-proxy-data"
    "$root/resolve.sh" "$name" || return 1
    "$state/core" --uri-fd 3 --listen-port 1080 --connect-timeout-ms 12000 --xray-version 26.11.9 3<"$state/active.uri" > "$state/core.log" 2>&1 &
    core=$!
    for tick in 1 2 3 4 5; do
        kill -0 "$core" 2>/dev/null || return 1
        grep -q '0100007F:0438 .* 0A ' /proc/net/tcp && return 0
        wait_seconds 1
    done
    return 1
}
next_node() {
    case "$name" in
        primary) name=backup-1;;
        backup-1) name=backup-2;;
        backup-2) name=backup-3;;
        backup-3) name=primary;;
    esac
}
previous=
name=primary
failed=0
set_status offline starting
while :; do
    if ! wan_present; then
        set_status offline usb
        stop_child "$core"; core=; name=primary; failed=0
        "$root/network.sh" prepare >/dev/null 2>&1 || :
        wait_seconds 4
        continue
    fi
    if ! iptables -t nat -nL NEWMAN_USB >/dev/null 2>&1 ||
       test "$(iptables -S FORWARD | head -1)" != '-P FORWARD DROP' ||
       test "$(ip6tables -S FORWARD | head -1)" != '-P FORWARD DROP'; then
        set_status offline network
        "$root/network.sh" prepare >/dev/null 2>&1 || :
        wait_seconds 2
        continue
    fi
    if test -z "$core" || ! kill -0 "$core" 2>/dev/null; then
        set_status checking connecting
        start_core || :
    fi
    if test -n "$core" && kill -0 "$core" 2>/dev/null && probe; then
        if test -z "$forward" || ! kill -0 "$forward" 2>/dev/null; then
            "$root/forward" </dev/null >/dev/null 2>&1 & forward=$!
            wait_seconds 1
        fi
        if kill -0 "$forward" 2>/dev/null; then
            set_status online "$evidence"
            failed=0
            size=$(wc -c < "$state/core.log")
            if test "$size" -gt 65536; then : > "$state/core.log"; fi
            wait_seconds 15
            continue
        fi
        set_status offline forwarding
    else
        set_status offline probe
    fi
    failed=$(expr "$failed" + 1)
    if test "$failed" -lt 2; then wait_seconds 15; continue; fi
    stop_child "$core"; core=; failed=0
    next_node
    if test "$name" = primary; then
        set_status offline retry
        wait_seconds 15
    fi
done
