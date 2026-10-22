#!/bin/sh
# Запускається всередині alpine-client. Створює три "пристрої" (network namespace),
# кожен у своєму VLAN на інтерфейсі eth0 (Host-only, теговані кадри до OPNsense):
#   dev-student -> VLAN 10, dev-teacher -> VLAN 20, dev-guest -> VLAN 30
# Адреса й шлюз приходять по DHCP від OPNsense. Повторний запуск безпечний.
set -e
modprobe 8021q
ip link set eth0 up

make_dev() {  # ім'я, vlan-id
  ns="$1"; id="$2"; ifn="v$id"
  # зупиняємо процеси старого namespace (HTTP-сервери тестів), інакше він не видаляється
  for p in $(ip netns pids "$ns" 2>/dev/null); do kill "$p" 2>/dev/null || true; done
  sleep 1
  if [ -e "/run/netns/$ns" ]; then
    ip netns del "$ns" 2>/dev/null || { umount "/run/netns/$ns" 2>/dev/null || true; rm -f "/run/netns/$ns"; }
  fi
  ip link del "eth0.$id" 2>/dev/null || true
  ip netns add "$ns"
  ip link add link eth0 name "$ifn" type vlan id "$id"
  ip link set "$ifn" netns "$ns"
  mkdir -p "/etc/netns/$ns"
  ip netns exec "$ns" ip link set lo up
  ip netns exec "$ns" ip link set "$ifn" up
  # DHCP: адреса, шлюз за замовчуванням і DNS від OPNsense
  ip netns exec "$ns" udhcpc -i "$ifn" -n -q -t 5 -T 2 \
     -s /usr/share/udhcpc/default.script >/tmp/dhcp-$ns.log 2>&1 || echo "DHCP FAIL $ns"
  echo "== $ns (VLAN $id)"; ip netns exec "$ns" ip -4 -o addr show "$ifn" | awk '{print "  ip:", $4}'
  ip netns exec "$ns" ip route | sed 's/^/  route: /'
}

make_dev dev-student 10
make_dev dev-teacher 20
make_dev dev-guest   30
