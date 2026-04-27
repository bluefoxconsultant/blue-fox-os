# Blue Fox OS - Anaconda Kickstart minimal (P3.2)
# Installer le minimum requis ; le welcome agent s'occupe du reste au firstboot.
#
# Validation locale : ksvalidator install/bf-os.ks
# Test : booter l'ISO Kinoite et fournir ce KS via boot param `inst.ks=...`.

# Langue : présenter le choix bilingue ; locale par defaut fr_CA.
lang fr_CA.UTF-8 --addsupport=en_CA.UTF-8

# Clavier : francais Canada par defaut, US disponible.
keyboard --vckeymap=ca --xlayouts='ca','us' --switch=grp:alt_shift_toggle

# Timezone : Montreal (les utilisateurs en NZ se reconfigureront).
timezone America/Montreal --utc

# Reseau : DHCP partout.
network --bootproto=dhcp --activate

# Comptes : aucun root password ; user créé interactivement.
rootpw --lock
user --groups=wheel --name=bfuser --gecos="Blue Fox User"

# Disque : entier, btrfs sur LUKS forcé. Pas de choix offert.
zerombr
clearpart --all --initlabel --drives=sda
ignoredisk --only-use=sda
autopart --type=btrfs --encrypted --passphrase=CHANGE_ME_AT_FIRSTBOOT --nohome

# Bootloader : EFI standard.
bootloader --location=mbr

# Pas de telemetrie.
firstboot --disable
selinux --enforcing
firewall --enabled

# Installation : on installe juste assez pour booter sur l'image OCI ;
# `rpm-ostree rebase` pendant le firstboot pull la vraie image BF.
ostreesetup --osname=fedora --remote=fedora --url=file:///run/install/repo/ostree/repo --ref=fedora/41/x86_64/kinoite

# Reboot apres install ; firstboot service prend le relais.
reboot --eject

%post --erroronfail --log=/var/log/bf-os-post.log
# Configuration post-install minimale ; tout le reste vit dans l'image OCI.

# 1. Activer le rebase automatique vers l'image BF.
ostree remote add bluefox https://ghcr.io/bluefoxconsultant/blue-fox-os-bf || true

# 2. Activer apt unattended... non, c'est rpm-ostreed-automatic ici.
systemctl enable rpm-ostreed-automatic.timer

# 3. Marquer pour le firstboot que le rebase BF doit s'executer.
mkdir -p /var/lib/bluefox-welcome
touch /var/lib/bluefox-welcome/needs-rebase

%end
