#!/bin/sh
set -e
PATH=/bin:/sbin:/usr/bin:/usr/sbin
export PATH
umask 077
root=/etc_rw/newman-usb
state=/tmp/newman-usb
running() {
    test -s "$state/manager.pid" || return 1
    pid=$(cat "$state/manager.pid")
    case "$pid" in ''|*[!0-9]*) return 1;; esac
    test -r "/proc/$pid/cmdline" && tr '\000' ' ' < "/proc/$pid/cmdline" | grep -q '/etc_rw/newman-usb/manager.sh'
}
case "$1" in
    start)
        trap '' HUP
        mkdir -p "$state"
        test -x "$state/core" || exit 2
        for profile in primary backup-1 backup-2 backup-3; do
            test -s "$root/nodes/$profile" || exit 2
        done
        if running; then echo RUNNING; exit 0; fi
        if ! mkdir "$state/starting" 2>/dev/null; then
            sleep 2
            running && exit 0
            rmdir "$state/starting" 2>/dev/null || :
            mkdir "$state/starting" || exit 1
        fi
        trap 'rmdir "$state/starting" 2>/dev/null || :' EXIT
        running && exit 0
        "$root/network.sh" prepare
        "$root/network.sh" red
        "$root/manager.sh" </dev/null > "$state/manager.log" 2>&1 & pid=$!
        echo "$pid" > "$state/manager.pid"
        sleep 1
        running || exit 3
        echo STARTED
        ;;
    status)
        if running; then cat "$state/status"; else echo 'offline - stopped'; fi
        if test -s "$state/traffic"; then cat "$state/traffic"; fi
        ;;
    stop)
        if running; then
            kill "$pid"
            for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
                running || break
                sleep 1
            done
            if running; then exit 3; fi
        fi
        "$root/network.sh" red
        echo STOPPED
        ;;
    *) exit 2;;
esac
