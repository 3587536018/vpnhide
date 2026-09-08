#!/system/bin/sh
# Installer hook run by KernelSU/Magisk. Called after the module files
# have been extracted to $MODPATH. Anything we write to $MODPATH persists
# into /data/adb/modules/<id>/ after the installer finishes.

# shellcheck disable=SC2034
SKIPUNZIP=0
MOD_VER="$(grep '^version=' "$MODPATH/module.prop" | cut -d= -f2)"
ui_print "- VPN Hide (Zygisk) ${MOD_VER:-unknown}"
ui_print "- Installing to $MODPATH"

# Make the native libraries readable/executable by zygote (one per ABI:
# arm64-v8a for 64-bit processes, armeabi-v7a for 32-bit ones).
set_perm "$MODPATH/zygisk/arm64-v8a.so" 0 0 0755
[ -f "$MODPATH/zygisk/armeabi-v7a.so" ] && set_perm "$MODPATH/zygisk/armeabi-v7a.so" 0 0 0755
# Keep only the activator matching this device's CPU. The zip ships both an
# arm64 and an armv7 build (issue #318): a 64-bit-only device cannot exec the
# armv7 binary, a 32-bit device cannot exec the arm64 one. $ARCH is set by
# Magisk/KSU/APatch (arm64 | arm | x86 | x64); fall back to uname -m if unset.
VH_ARCH="$ARCH"
if [ -z "$VH_ARCH" ]; then
  case "$(uname -m)" in
    aarch64 | arm64) VH_ARCH=arm64 ;;
    arm*) VH_ARCH=arm ;;
    *) VH_ARCH=arm64 ;;
  esac
fi
if [ "$VH_ARCH" = "arm" ]; then
  mv -f "$MODPATH/activator.armv7" "$MODPATH/activator"
else
  mv -f "$MODPATH/activator.arm64" "$MODPATH/activator"
fi
rm -f "$MODPATH/activator.arm64" "$MODPATH/activator.armv7"

set_perm "$MODPATH/activator" 0 0 0755
set_perm "$MODPATH/service.sh" 0 0 0755
set_perm "$MODPATH/uninstall.sh" 0 0 0755

ui_print "- Config: /data/system/vpnhide_config.json (managed by the app)"
ui_print "- Pick target apps via the VPN Hide app."
