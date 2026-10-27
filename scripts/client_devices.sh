#!/bin/sh
# Запускається всередині alpine-client. Створює три "пристрої" (network namespace),
# кожен у своєму VLAN на інтерфейсі eth0 (Host-only, теговані кадри до OPNsense):
#   dev-student -> VLAN 10, dev-teacher -> VLAN 20, dev-guest -> VLAN 30
# Адреса й шлюз приходять по DHCP від OPNsense. Повторний запуск безпечний.
set -e
modprobe 8021q
ip link set eth0 up
ip link set eth0 promisc on   # приймати кадри для власних MAC пристроїв (+ Promiscuous Mode: Allow All у VirtualBox)

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
  # власна MAC для кожного пристрою (портал відстежує сесії за MAC): macvlan на eth0 з унікальною MAC,
  # а VLAN створюється вже всередині пристрою — так в одному VLAN може бути багато клієнтів (load_clients.sh)
  ip link add link eth0 name "mv-$ns" address "${3:-02:67:00:00:$id:01}" type macvlan mode bridge
  ip link set "mv-$ns" netns "$ns"
  # власний resolv.conf: інакше DHCP кожного пристрою переписує спільний /etc/resolv.conf і пристрій
  # отримує DNS шлюзу чужого сегмента (до якого firewall не пускає)
  mkdir -p "/etc/netns/$ns"; : > "/etc/netns/$ns/resolv.conf"
  ip netns exec "$ns" ip link set lo up
  ip netns exec "$ns" ip link set "mv-$ns" name lan0
  ip netns exec "$ns" ip link set lan0 up
  ip netns exec "$ns" ip link add link lan0 name "$ifn" type vlan id "$id"
  ip netns exec "$ns" ip link set "$ifn" up
  # DHCP: адреса, шлюз за замовчуванням і DNS від OPNsense
  ip netns exec "$ns" udhcpc -i "$ifn" -n -q -t 5 -T 2 \
     -s /usr/share/udhcpc/default.script >/tmp/dhcp-$ns.log 2>&1 || echo "DHCP FAIL $ns"
  # DNS — шлюз свого сегмента (скрипт udhcpc не може замінити змонтований resolv.conf простору імен)
  echo "nameserver 10.67.$id.1" > "/etc/netns/$ns/resolv.conf"
  echo "== $ns (VLAN $id)"; ip netns exec "$ns" ip -4 -o addr show "$ifn" | awk '{print "  ip:", $4}'
  ip netns exec "$ns" ip route | sed 's/^/  route: /'
}

make_ap() {  # змодельована точка доступу в мережі керування (LAN): ім'я, IP. Її пінгує моніторинг OPNsense
  ns="$1"; ip="$2"
  [ -e "/run/netns/$ns" ] && ip netns del "$ns" 2>/dev/null
  ip netns add "$ns"
  # постійна MAC, як у справжньої точки: з випадковою після перезапуску шлюз тримав стару MAC в ARP
  ip link add link eth0 name "mv-$ns" address "02:67:00:99:00:0${ns#ap-}" type macvlan mode bridge
  ip link set "mv-$ns" netns "$ns"
  ip netns exec "$ns" ip link set lo up
  ip netns exec "$ns" ip link set "mv-$ns" name mgmt0
  ip netns exec "$ns" ip addr add "$ip/24" dev mgmt0
  ip netns exec "$ns" ip link set mgmt0 up
  echo "== $ns: $ip"
}

make_dev dev-student 10
make_dev dev-teacher 20
make_dev dev-guest   30
# Sprint 2: три точки доступу коворкінгу (збій моделюється командою: ip netns exec ap-2 ip link set mgmt0 down)
make_ap ap-1 192.168.56.31
make_ap ap-2 192.168.56.32
make_ap ap-3 192.168.56.33
