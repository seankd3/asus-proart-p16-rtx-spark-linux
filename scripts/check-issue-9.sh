#!/usr/bin/env bash
# Acceptance for issue 9, run once after a boot into the patched kernel.
# $1 is a file with the owner's live results, one name=value line each:
#   mute=KEY_MUTE                    the codes one press makes in libinput debug-events,
#   volumedown=KEY_VOLUMEDOWN        so two events from one press read KEY_MUTE KEY_MUTE
#   volumeup=KEY_VOLUMEUP
#   backlight=1 2 3 0 1              asus::kbd_backlight level before and after each of four presses
#   fn_esc=KEY_MUTE KEY_F1 KEY_MUTE  the Mute key's code before and after each of two Fn+Esc presses
source "$(dirname "$0")/common.sh"
release=$(python3 "$REPO/scripts/p16_release.py" --shell)
eval "$release"
results=$(<"${1:?Usage: check-issue-9.sh RESULTS}")
fail() { echo "FAIL: $*" >&2; exit 1; }
value() { sed -n "s/^$1=//p" <<<"$results"; }

[[ $(uname -r) == "$RELEASE" ]] || fail "kernel is $(uname -r), not $RELEASE"
[[ $(readlink -f /sys/bus/platform/drivers/asus-nb-wmi/asus-nb-wmi) == /sys/devices/platform/asus-nb-wmi ]] ||
  fail 'asus-nb-wmi is not bound'
log=$(journalctl -k -b -o cat --no-pager)
[[ $log == *'input: Asus WMI hotkeys'* ]] || fail 'the kernel log of this boot has no asus-wmi probe'
[[ $log != *'Unable to install key filter'* ]] || fail 'asus-wmi tried to install the i8042 key filter'

for key in mute volumedown volumeup; do
  [[ $(value $key) == "KEY_${key^^}" ]] || fail "$key: one press must make one KEY_${key^^}, not '$(value $key)'"
done

backlight=$(value backlight)
[[ $backlight =~ ^[0-3]( [0-3]){4}$ ]] || fail 'backlight: record the level before and after each of four presses'
read -ra level <<<"$backlight"
for i in 1 2 3 4; do
  (( level[i] == (level[i-1] + 1) % 4 )) || fail "backlight: press $i went from ${level[i-1]} to ${level[i]}"
done

fn_esc=$(value fn_esc)
[[ $fn_esc =~ ^(KEY_MUTE (KEY_F[0-9]+)|(KEY_F[0-9]+) KEY_MUTE)\ ([A-Z0-9_]+)$ ]] &&
  [[ ${BASH_REMATCH[4]} == "${fn_esc%% *}" ]] ||
  fail "fn_esc: the Mute key must toggle between KEY_MUTE and one KEY_Fn and back, not '$fn_esc'"

echo "PASS: issue 9 on $RELEASE"
