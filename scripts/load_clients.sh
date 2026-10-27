#!/bin/sh
# Запускається всередині alpine-client. Sprint 2, TC-02: змодельоване пікове навантаження.
#   sh load_clients.sh up <студентів> <викладачів> <гостей>   -> створити клієнтів ld-s01.., ld-t01.., ld-g01..
#   sh load_clients.sh down                                   -> прибрати всіх
# Кожен клієнт — окремий network namespace з власною MAC (02:67:01:<vlan>:00:<n>) і адресою по DHCP.
# Вивід: "<namespace> <ip>" або "<namespace> DHCP-FAIL".

down() {
  for ns in $(ip netns list | awk '/^ld-/{print $1}'); do
    for p in $(ip netns pids "$ns" 2>/dev/null); do kill "$p" 2>/dev/null; done
    ip netns del "$ns" 2>/dev/null
    rm -rf "/etc/netns/$ns"
  done
}

client() {  # namespace, vlan-id, номер — створення інтерфейсів (послідовно: паралельно ip netns дає збої)
  ns="$1"; id="$2"; n="$(printf %02x "$3")"
  ip netns add "$ns"
  mkdir -p "/etc/netns/$ns"; : > "/etc/netns/$ns/resolv.conf"   # власний DNS (від свого шлюзу)
  ip link add link eth0 name "mv-$ns" address "02:67:01:$id:00:$n" type macvlan mode bridge
  ip link set "mv-$ns" netns "$ns"
  ip netns exec "$ns" sh -c "ip link set lo up; ip link set mv-$ns name lan0; ip link set lan0 up;
    ip link add link lan0 name v$id type vlan id $id; ip link set v$id up"
}

dhcp() {  # namespace, vlan-id — отримання адреси (паралельно для всіх клієнтів)
  ns="$1"; id="$2"
  if ip netns exec "$ns" udhcpc -i "v$id" -n -q -t 8 -T 3 -s /usr/share/udhcpc/default.script >/dev/null 2>&1; then
    # DNS — шлюз свого сегмента (скрипт udhcpc не може замінити змонтований resolv.conf простору імен)
    echo "nameserver 10.67.$id.1" > "/etc/netns/$ns/resolv.conf"
    echo "$ns $(ip netns exec "$ns" ip -4 -o addr show "v$id" | awk '{print $4}' | cut -d/ -f1)"
  else
    echo "$ns DHCP-FAIL"
  fi
}

case "$1" in
  up)
    down
    ip link set eth0 promisc on
    for spec in "s 10 $2" "t 20 $3" "g 30 $4"; do
      set -- $spec
      i=1; while [ "$i" -le "$3" ]; do client "$(printf "ld-$1%02d" "$i")" "$2" "$i"; i=$((i + 1)); done
    done
    # підключення хвилями по 10 пристроїв (люди приходять на подію не в одну мілісекунду;
    # 60 одночасних udhcpc перевантажують саму клієнтську VM, а не шлюз)
    k=0
    for ns in $(ip netns list | awk '/^ld-/{print $1}'); do
      case "$ns" in ld-s*) id=10 ;; ld-t*) id=20 ;; *) id=30 ;; esac
      dhcp "$ns" "$id" &
      k=$((k + 1)); [ $((k % 10)) -eq 0 ] && wait
    done > /tmp/ld-up.txt
    wait
    # як справжній пристрій: якщо адреса не прийшла, клієнт повторює DHCP (до 2 повторів)
    for try in 1 2; do
      for ns in $(awk '/DHCP-FAIL/{print $1}' /tmp/ld-up.txt); do
        case "$ns" in ld-s*) id=10 ;; ld-t*) id=20 ;; *) id=30 ;; esac
        sed -i "/^$ns /d" /tmp/ld-up.txt
        dhcp "$ns" "$id" | sed "s/\$/ retry$try/" >> /tmp/ld-up.txt
      done
    done
    cat /tmp/ld-up.txt ;;
  down) down ;;
  *) echo "usage: $0 up <s> <t> <g> | down"; exit 1 ;;
esac
