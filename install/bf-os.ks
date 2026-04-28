# Blue Fox OS - Anaconda Kickstart (P3.2 - matrice BFOSP2)
#
# Strategie : le KS pre-remplit les choix non-sensibles (locale, clavier, timezone,
# disque, ostreesetup) et laisse Anaconda demander le reste interactivement
# (passphrase LUKS, hostname, user). Le rebase vers l'image BF est differe au
# firstboot via le sentinelle /var/lib/bluefox-welcome/needs-rebase, qu'un
# service systemd livre par P4.1 (welcome agent) consomme.
#
# Validation locale : ksvalidator install/bf-os.ks
# Test : booter une ISO Kinoite officielle et fournir ce KS via inst.ks=...

# Langue : choix bilingue, locale par defaut fr_CA.
lang fr_CA.UTF-8 --addsupport=en_CA.UTF-8

# Clavier : francais Canada par defaut, US disponible (Alt+Shift pour switch).
keyboard --vckeymap=ca --xlayouts='ca','us' --switch=grp:alt_shift_toggle

# Timezone : Montreal (utilisateurs en NZ se reconfigureront via GNOME settings).
timezone America/Montreal --utc

# Reseau : DHCP partout. Hostname laisse a Anaconda ; le welcome agent (P4.1)
# le renomme post-firstboot suivant le pattern {tenant-slug}-{firstname} (BFOSP2).
network --bootproto=dhcp --activate

# Comptes : root verrouille, user cree interactivement par Anaconda
# (KS ne hardcode pas de credentials).
rootpw --lock

# Disque : btrfs sur LUKS, passphrase saisie au wizard Anaconda.
# Pas de TPM bind en v1 (decision : keep it boring, Pablo doit pouvoir relire).
zerombr
clearpart --all --initlabel
autopart --type=btrfs --encrypted --nohome

# Bootloader : pas de --location, Anaconda detecte EFI vs BIOS automatiquement.
bootloader --timeout=3

# Securite : SELinux enforcing, firewall actif, pas de telemetrie.
selinux --enforcing
firewall --enabled

# Installation : on pose un Kinoite stock depuis l'ISO ;
# rpm-ostree rebase vers l'image BF est differe au firstboot.
ostreesetup --osname=fedora --remote=fedora --url=file:///run/install/repo/ostree/repo --ref=fedora/41/x86_64/kinoite

# Reboot apres install ; firstboot prend le relais.
reboot --eject

%post --erroronfail --log=/var/log/bf-os-post.log
# Post-install minimal : tout le vrai branding vit dans l'image OCI BF
# qu'on rebase au firstboot.

# 1. Activer les MAJ rpm-ostree automatiques.
systemctl enable rpm-ostreed-automatic.timer

# 2. Sentinelle : signale au welcome agent (P4.1) qu'il faut faire
# `rpm-ostree rebase ostree-image-signed:docker://ghcr.io/bluefoxconsultant/blue-fox-os-bf:latest`.
# Le service systemd qui consomme ce sentinelle (bluefox-rebase.service)
# est livre par P4.1 ; tant qu'il n'existe pas, le sentinelle reste sans effet.
mkdir -p /var/lib/bluefox-welcome
touch /var/lib/bluefox-welcome/needs-rebase

%end
