#!/bin/bash
# Essai du BUREAU dans la VM (vm-ventoy.sh, vm-profil.sh), en root, sans QR
# (#26870, 2026-10-24). ESSAI SEULEMENT : modifie greetd et PAM en mémoire vive.
#   bash essai-bureau-vm.sh ouvrir    session Plasma de « essai » (initial_session de greetd)
#   bash essai-bureau-vm.sh pwa       lanceurs + épinglage, Brave --app, fenêtres vues par KWin
#   bash essai-bureau-vm.sh dolphin   emplacement Nextcloud, Dolphin ouvert
set -uo pipefail
U=essai; UID_E=23456
envu() { runuser -u $U -- env XDG_RUNTIME_DIR=/run/user/$UID_E \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$UID_E/bus WAYLAND_DISPLAY=wayland-0 \
  XDG_SESSION_TYPE=wayland "$@"; }
case "$1" in
ouvrir)
  getent passwd $U >/dev/null || useradd -m -u $UID_E -G bfos-live $U
  mkdir -p /home/$U/.config/bluefox-welcome && touch /home/$U/.config/bluefox-welcome/done
  chown -R $U: /home/$U/.config
  # Essai sans QR : pam_permit à la place de pam_bfos (sans module d'auth, pam_setcred
  # rend CRED_ERR et greetd meurt en boucle). L'initial_session ne joue qu'une fois par
  # démarrage de la machine : on efface le témoin de greetd.
  sed -i -e 's/^auth .*pam_bfos\.so.*/auth       required    pam_permit.so/' \
    -e '/^session .*pam_bfos\.so/d' /etc/pam.d/greetd
  rm -f /run/greetd.run; systemctl reset-failed greetd
  grep -q initial_session /etc/greetd/config.toml || cat >> /etc/greetd/config.toml <<EOF

[initial_session]
command = "/usr/libexec/plasma-dbus-run-session-if-needed /usr/bin/startplasma-wayland"
user = "$U"
EOF
  systemctl restart greetd; sleep 45
  loginctl list-sessions --no-legend; pgrep -u $U -a plasmashell | head -2; ls /run/user/$UID_E
  ;;
pwa)
  W=$(python3 -I -c 'import bluefox_welcome, os; print(os.path.dirname(os.path.dirname(bluefox_welcome.__file__)))')
  envu python3 -I -c "
import sys; sys.path.insert(0, '$W')
from bluefox_welcome.apply.pwa_lanceurs import appliquer_pwa_epinglees
print(appliquer_pwa_epinglees([
  {'name': 'Odoo', 'url': 'https://bluefoxconsultant.com/odoo', 'pinned': True},
  {'name': 'Nextcloud', 'url': 'https://nextcloud.bluefoxconsultant.com/apps/files', 'pinned': True},
  {'name': 'Vaultwarden', 'url': 'https://vault.bluefoxconsultant.com', 'pinned': True},
  {'name': 'Nextcloud Talk', 'url': 'https://nextcloud.bluefoxconsultant.com/index.php/apps/spreed', 'pinned': True}]))"
  ls /home/$U/.local/share/applications/ /home/$U/.local/share/icons/bfos-pwa/
  grep -n "launchers" /home/$U/.config/plasma-org.kde.plasma.desktop-appletsrc
  envu setsid flatpak run --command=brave com.brave.Browser --profile-directory=Default \
    --no-first-run --app=https://bluefoxconsultant.com/odoo >/dev/null 2>&1 &
  envu setsid flatpak run --command=brave com.brave.Browser --profile-directory=Default \
    --no-first-run --app="https://bluefoxconsultant.com/odoo?bfos_login=essai" >/dev/null 2>&1 &
  sleep 25
  cat > /tmp/liste.js <<'EOF'
var ws = workspace.windowList ? workspace.windowList() : workspace.clientList();
for (var i = 0; i < ws.length; i++) {
  var w = ws[i];
  print("BFOSWIN|" + w.resourceClass + "|" + w.resourceName + "|" + w.desktopFileName + "|" + w.caption);
}
EOF
  chmod 644 /tmp/liste.js
  envu gdbus call --session --dest org.kde.KWin --object-path /Scripting \
    --method org.kde.kwin.Scripting.loadScript /tmp/liste.js bfosliste$RANDOM
  envu gdbus call --session --dest org.kde.KWin --object-path /Scripting --method org.kde.kwin.Scripting.start
  sleep 3
  journalctl -b --no-pager -o cat _UID=$UID_E | grep BFOSWIN | tail -12
  ;;
dolphin)
  W=$(python3 -I -c 'import bluefox_welcome, os; print(os.path.dirname(os.path.dirname(bluefox_welcome.__file__)))')
  envu mkdir -p /home/$U/Nextcloud
  envu python3 -I -c "
import sys; sys.path.insert(0, '$W')
from pathlib import Path
from bluefox_welcome.apply.emplacements import ajouter_emplacements
print(ajouter_emplacements([('Nextcloud', Path('/home/$U/Nextcloud'))]))"
  envu setsid dolphin >/dev/null 2>&1 &
  sleep 12
  grep -o 'href="[^"]*"' /home/$U/.local/share/user-places.xbel
  ;;
esac
