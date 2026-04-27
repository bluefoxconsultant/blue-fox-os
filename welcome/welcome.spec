Name:           bluefox-welcome
Version:        0.1.0
Release:        1%{?dist}
Summary:        Blue Fox OS first-boot welcome agent

License:        MIT
URL:            https://github.com/bluefoxconsultant/blue-fox-os
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  systemd-rpm-macros

Requires:       python3
Requires:       python3-qt6
Requires:       systemd
Requires:       kaccounts-providers

%description
Agent de premier démarrage pour Blue Fox OS. Wizard 4-5 écrans
qui demande le courriel BF de l'utilisateur, fetch la config tenant
depuis config.bluefoxconsultant.com/{slug}.json, et configure
KAccounts (Nextcloud), Thunderbird (Migadu), Bitwarden, NC Talk,
CalDAV, CardDAV, et l'imprimante réseau.

%prep
%autosetup -n %{name}-%{version}

%build
%py3_build

%install
%py3_install

# Service systemd
mkdir -p %{buildroot}%{_unitdir}
install -m 0644 firstboot.service %{buildroot}%{_unitdir}/firstboot.service

# Marqueur d'état
mkdir -p %{buildroot}%{_localstatedir}/lib/bluefox-welcome

%files
%license LICENSE
%doc README.md
%{_bindir}/bluefox-welcome
%{python3_sitelib}/bluefox_welcome/
%{python3_sitelib}/bluefox_welcome-%{version}-py%{python3_version}.egg-info/
%{_unitdir}/firstboot.service
%dir %{_localstatedir}/lib/bluefox-welcome

%post
%systemd_post firstboot.service

%preun
%systemd_preun firstboot.service

%postun
%systemd_postun_with_restart firstboot.service

%changelog
* Mon Apr 27 2026 Olivier Morneau <olivier@bluefoxconsultant.com> - 0.1.0-1
- Squelette initial.
