Name:           spms
Version:        1.2.2
Release:        1%{?dist}
Summary:        SPMS (Super Property Management System) web application

License:        Proprietary
URL:            https://github.com/Michaelhehelmy/spms
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch
Requires:       python3 >= 3.10
Requires:       python3-pip
Requires:       python3-virtualenv
%{?systemd_requires}

%description
Local-first property management web app (FastAPI/HTMX, SQLite).
Installs application files under /opt/spms and a systemd service.
Create a virtualenv and install dependencies after install, then configure
/etc/spms/spms.env.

%prep
%setup -q -n %{name}-%{version}

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/spms %{buildroot}/etc/spms %{buildroot}/var/lib/spms %{buildroot}/usr/lib/systemd/system
cp -a * %{buildroot}/opt/spms/

cat > %{buildroot}/usr/lib/systemd/system/spms-web.service <<'EOF'
[Unit]
Description=SPMS Web UI (FastAPI/HTMX, SQLite-safe)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=spms
Group=spms
WorkingDirectory=/opt/spms
EnvironmentFile=/etc/spms/spms.env
Environment=PYTHONPATH=/opt/spms/src:/opt/spms
ExecStart=/opt/spms/.venv/bin/python -m uvicorn ui.web.server:app --host ${HOST} --port ${PORT} --workers 1
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

cat > %{buildroot}/etc/spms/spms.env <<'EOF'
PM_DATA_DIR=/var/lib/spms
PM_WEB_SECRET_KEY=change-me-to-a-long-random-secret
PM_WEB_COOKIE_SECURE=1
HOST=127.0.0.1
PORT=8000
PYTHONPATH=/opt/spms/src:/opt/spms
EOF

%pre
getent group spms >/dev/null || groupadd -r spms || :
getent passwd spms >/dev/null || useradd -r -g spms -d /opt/spms -s /sbin/nologin spms || :

%post
%systemd_post spms-web.service
mkdir -p /var/lib/spms
chown -R spms:spms /var/lib/spms || :
if [ ! -f /etc/spms/spms.env ]; then
  : # packaged default installed as config
fi
echo "SPMS installed to /opt/spms"
echo "Next: python3 -m venv /opt/spms/.venv && /opt/spms/.venv/bin/pip install -r /opt/spms/requirements.txt"
echo "Edit /etc/spms/spms.env then: systemctl enable --now spms-web"

%preun
%systemd_preun spms-web.service

%postun
%systemd_postun_with_restart spms-web.service

%files
%dir /opt/spms
/opt/spms/*
%dir /var/lib/spms
%config(noreplace) /etc/spms/spms.env
/usr/lib/systemd/system/spms-web.service

%changelog
* Fri Nov 20 2026 SPMS Maintainers <noreply@example.com> - 1.2.2-1
- CI/build fixes (yamllint, native smoke test, container hash pin) and version bump
* Fri Nov 20 2026 SPMS Maintainers <noreply@example.com> - 1.2.1-1
- Version bump and packaging/modularity updates
* Fri Nov 20 2026 SPMS Maintainers <noreply@example.com> - 1.2.0-1
- Initial RPM packaging scaffold