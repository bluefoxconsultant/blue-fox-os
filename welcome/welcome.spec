Name:           bluefox-welcome
Version:        0.2.0
Release:        1%{?dist}
Summary:        Blue Fox OS first-boot welcome agent

License:        MIT
URL:            https://github.com/bluefoxconsultant/blue-fox-os
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros

Requires:       python3
Requires:       python3-pyqt6
Requires:       systemd
Requires:       kaccounts-providers
Requires:       rclone
Requires:       fuse3

%description
Agent de premier démarrage pour Blue Fox OS. Wizard 4-5 écrans
qui demande le courriel BF de l'utilisateur, fetch la config tenant
depuis config.bluefoxconsultant.com/{slug}.json, et configure
KAccounts (Nextcloud), Thunderbird (Migadu), Bitwarden, NC Talk,
CalDAV, CardDAV, et l'imprimante réseau.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
# -R : on exclut les Requires-Dist runtime du pyproject (PyQt6, requests,
# jsonschema). PyPI names != Fedora package names ; les Requires: explicites
# du spec (python3-qt6 etc.) sont la source de verite runtime.
%pyproject_buildrequires -R

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files bluefox_welcome

# Service systemd
mkdir -p %{buildroot}%{_unitdir}
install -m 0644 firstboot.service %{buildroot}%{_unitdir}/firstboot.service

# Marqueur d'état
mkdir -p %{buildroot}%{_localstatedir}/lib/bluefox-welcome

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/bluefox-welcome
%{_unitdir}/firstboot.service
%dir %{_localstatedir}/lib/bluefox-welcome

%post
%systemd_post firstboot.service

%preun
%systemd_preun firstboot.service

%postun
%systemd_postun_with_restart firstboot.service

%changelog
* Tue Apr 28 2026 Olivier Morneau <olivier@bluefoxconsultant.com> - 0.2.0-1
- Wire P4.1 integrations: rclone mount, kcmshell6 KAccounts launcher,
  Bitwarden Flatpak prefs, Brave managed policy. Add tests + apply/ submodule.

* Mon Apr 27 2026 Olivier Morneau <olivier@bluefoxconsultant.com> - 0.1.0-1
- Squelette initial.
