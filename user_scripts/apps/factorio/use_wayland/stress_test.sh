#!/usr/bin/env bash
# Stress tests for fix_run_on_wayland.py on the real game dir.
# Shim layout (new persistent scheme):
#   primary : ~/.factorio/wayland_fix/libEGL.so.1
#   sandbox : ~/Games/jc141/native-docs/.factorio/wayland_fix/libEGL.so.1
#   wiring  : $GAME/local.config  ENV="env LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1"
GAME=/mnt/zram1/Factorio_2.1.14
PRIMARY=~/.factorio/wayland_fix
SANDBOX=~/Games/jc141/native-docs/.factorio/wayland_fix
ENV_NEW='ENV="env LD_PRELOAD=$HOME/.factorio/wayland_fix/libEGL.so.1"'
cd "$(dirname "$0")" || exit 1
FIX=fix_run_on_wayland.py
PASS=0; FAIL=0
ok()   { echo "[PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "[FAIL] $1"; FAIL=$((FAIL+1)); }

echo "===== TEST 3: break (delete shims + unset ENV) then repair ====="
rm -f "$PRIMARY/libEGL.so.1" "$SANDBOX/libEGL.so.1"
sed -i 's|^ENV=.*|#ENV=REMOVED_BY_TEST|' "$GAME/local.config"
python3 "$FIX" --skip-packages > /tmp/t3.log 2>&1
grep -q 'SMOKE TEST PASSED' /tmp/t3.log && ok "repair: shim rebuilt + smoke passed" || { bad "repair failed"; tail -5 /tmp/t3.log; }
grep -q 'ENV="env LD_PRELOAD=\$HOME/.factorio/wayland_fix/libEGL.so.1"' "$GAME/local.config" && ok "repair: ENV line restored" || bad "ENV line missing"
[ -f "$PRIMARY/libEGL.so.1" ] && ok "repair: primary libEGL.so.1 present" || bad "primary shim missing after repair"
[ -f "$SANDBOX/libEGL.so.1" ] && ok "repair: sandbox mirror present" || bad "sandbox shim missing after repair"
cmp -s "$PRIMARY/libEGL.so.1" "$SANDBOX/libEGL.so.1" && ok "repair: copies identical" || bad "copies differ"

echo "===== TEST 4: fresh-install simulation (whole wayland_fix dirs gone) ====="
mv "$PRIMARY" "/tmp/primary_backup_$$" 2>/dev/null || { mkdir -p /tmp/primary_backup_$$; }
mv "$SANDBOX" "/tmp/sandbox_backup_$$" 2>/dev/null || { mkdir -p /tmp/sandbox_backup_$$; }
sed -i 's|^ENV=.*|#ENV=REMOVED_BY_TEST|' "$GAME/local.config"
python3 "$FIX" --skip-packages > /tmp/t4.log 2>&1
[ -f "$PRIMARY/libEGL.so.1" ] && grep -q 'SMOKE TEST PASSED' /tmp/t4.log \
  && ok "fresh: full rebuild from embedded source + smoke passed" || { bad "fresh install failed"; tail -8 /tmp/t4.log; }
[ -f "$PRIMARY/eglfix.c" ] && [ -f "$PRIMARY/export.map" ] && ok "fresh: sources written alongside shim" || bad "sources missing"
[ -f "$SANDBOX/libEGL.so.1" ] && ok "fresh: sandbox mirror rebuilt" || bad "sandbox mirror missing"
grep -q '^ENV="env LD_PRELOAD=' "$GAME/local.config" && ok "fresh: ENV wired" || bad "ENV not wired"
# verify the script-rebuilt shim source is byte-identical to our reference copy
if cmp -s "$PRIMARY/eglfix.c" /tmp/primary_backup_$$/eglfix.c; then
  ok "fresh: written eglfix.c identical to reference"
else
  bad "fresh: eglfix.c differs from reference"
fi
rm -rf /tmp/primary_backup_$$ /tmp/sandbox_backup_$$

echo "===== TEST 5: --force rebuild ====="
BEFORE=$(md5sum "$PRIMARY/libEGL.so.1" | cut -d' ' -f1)
sleep 1
python3 "$FIX" --skip-packages --force > /tmp/t5.log 2>&1
AFTER=$(md5sum "$PRIMARY/libEGL.so.1" | cut -d' ' -f1)
[ "$BEFORE" = "$AFTER" ] && ok "force rebuild is deterministic (same hash)" || bad "force rebuild changed hash"
grep -q 'SMOKE TEST PASSED' /tmp/t5.log && ok "force: smoke passed" || bad "force: smoke failed"

echo "===== TEST 6: idempotency (run twice, expect no rebuild / no config churn) ====="
M1=$(md5sum "$PRIMARY/libEGL.so.1" | cut -d' ' -f1)
C1=$(md5sum "$GAME/local.config" | cut -d' ' -f1)
python3 "$FIX" --skip-packages > /tmp/t6a.log 2>&1
python3 "$FIX" --skip-packages > /tmp/t6b.log 2>&1
M2=$(md5sum "$PRIMARY/libEGL.so.1" | cut -d' ' -f1)
C2=$(md5sum "$GAME/local.config" | cut -d' ' -f1)
[ "$M1" = "$M2" ] && ok "idempotent: shim unchanged across runs" || bad "shim changed"
[ "$C1" = "$C2" ] && ok "idempotent: local.config unchanged across runs" || bad "config changed"
grep -q 'already wired correctly' /tmp/t6b.log && ok "idempotent: no ENV churn reported" || bad "config churn"
grep -q 'already built and up to date' /tmp/t6b.log && ok "idempotent: no rebuild reported" || bad "rebuild reported on 2nd run"

echo "===== TEST 7: --check reports 0 problems after install ====="
python3 "$FIX" --check > /tmp/t7.log 2>&1
grep -q 'problems found: 0' /tmp/t7.log && ok "check: 0 problems" || { bad "check found problems"; tail -6 /tmp/t7.log; }

echo "===== TEST 8: --reset removes everything ====="
python3 "$FIX" --reset --yes > /tmp/t8.log 2>&1
[ ! -e "$PRIMARY" ] && [ ! -e "$SANDBOX" ] && ok "reset: both shim dirs removed" || bad "shim dirs remain after reset"
[ ! -f "$GAME/local.config" ] || grep -q '^ENV=' "$GAME/local.config" || ok "reset: config unwired" || bad "ENV still present after reset"
[ ! -f "$GAME/local.config.bak" ] && ok "reset: stray .bak removed" || bad ".bak left behind"

echo "===== TEST 9: reinstall after reset ====="
python3 "$FIX" --skip-packages > /tmp/t9.log 2>&1
[ -f "$PRIMARY/libEGL.so.1" ] && grep -q 'SMOKE TEST PASSED' /tmp/t9.log \
  && ok "reinstall: full rebuild + smoke passed" || { bad "reinstall failed"; tail -5 /tmp/t9.log; }

echo
echo "===== RESULT: $PASS passed, $FAIL failed ====="
[ "$FAIL" -eq 0 ]
