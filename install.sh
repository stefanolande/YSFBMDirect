#!/usr/bin/env bash

[ "$UID" -eq 0 ] || exec sudo "$0" "$@"

echo "Installing YSFBMDirect v2.0"
echo "============================"

# Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "Installing uv package manager..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
else
    echo "uv already installed"
fi

# Clean up old installation
rm -rf /tmp/YSFBMDirect

# Clone repository
cd /tmp/ || exit
echo "Cloning YSFBMDirect repository..."
git clone https://github.com/stefanolande/YSFBMDirect.git -q

# Create config.yaml from example
cp YSFBMDirect/config.example.yaml YSFBMDirect/config.yaml

# Get user input
while [[ $call == '' ]]; do
  read -rp "Enter your callsign: " call </dev/tty
done

while [[ $password == '' ]]; do
  read -rsp "Enter your Brandmeister password: " password </dev/tty
  echo ""
done

# Update config.yaml with user input
sed -i "s/callsign: YOUR_CALLSIGN/callsign: $call/" YSFBMDirect/config.yaml
sed -i "s/bm_password: YOUR_PASSWORD/bm_password: \"$password\"/" YSFBMDirect/config.yaml

echo ""
read -rp "Do you want to edit the Talk Group configuration [y/N]?" edit_tg </dev/tty
while [[ $edit_tg != 'y' ]] && [[ $edit_tg != 'Y' ]] && [[ $edit_tg != 'n' ]] && [[ $edit_tg != 'N' ]] && [[ $edit_tg != '' ]]; do
  read -rp "Do you want to edit the Talk Group configuration [y/N]?" edit_tg </dev/tty
done

if [[ $edit_tg == 'y' ]] || [[ $edit_tg == 'Y' ]];
then
  ${EDITOR:-nano} YSFBMDirect/config.yaml </dev/tty
else
  echo "You can edit it later in /opt/YSFBMDirect/config.yaml"
fi

# Install to /opt
echo "Installing to /opt/YSFBMDirect..."
rm -rf /opt/YSFBMDirect
cp -r YSFBMDirect /opt/

# Create virtual environment and install dependencies
cd /opt/YSFBMDirect/ || exit
echo "Creating virtual environment..."
uv venv

echo "Installing dependencies..."
uv pip install -e "."

# Install systemd service
echo "Installing systemd service..."
cp ysfbmdirect.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ysfbmdirect.service

# Add YSF-BM to YSFHosts.txt (pi-star specific)
ysf="01234;YSF-BM;YSF-BM;127.0.0.1;42002;001"
if [[ -f /root/YSFHosts.txt ]]; then
  if ! grep -q "$ysf" /root/YSFHosts.txt; then
    echo "$ysf" >> /root/YSFHosts.txt
    if command -v /usr/local/sbin/HostFilesUpdate.sh &> /dev/null; then
      /usr/local/sbin/HostFilesUpdate.sh
    fi
  fi
fi

# Start service
echo "Starting YSFBMDirect service..."
systemctl start ysfbmdirect.service

echo ""
echo "============================"
echo "YSFBMDirect v2.0 installed successfully!"
echo ""
echo "Configuration file: /opt/YSFBMDirect/config.yaml"
echo "Service status: systemctl status ysfbmdirect"
echo "View logs: journalctl -u ysfbmdirect -f"
echo "            or tail -f /var/log/pi-star/YSFBMDirect.log"
echo ""
echo "To manually start/stop the service:"
echo "  systemctl start ysfbmdirect"
echo "  systemctl stop ysfbmdirect"
echo "============================"
