#!/bin/bash

# Ensure running with root privileges
if [ "$EUID" -ne 0 ]
  then echo "Please run this script with root privileges"
  exit
fi

# Install dependencies
echo "Installing dependencies..."
apt-get update
apt-get install -y python3-pip udisks2 python3-yaml python3-pyudev python3-psutil python3-tqdm

# Install Python dependencies（tqdm is installed in Raspberry Pi OS）
# pip3 install tqdm

# Create log directory
mkdir -p /var/log

# Copy program files
echo "Installing program files..."
cp autocopy.py /usr/local/bin/
chmod +x /usr/local/bin/autocopy.py

# Copy configuration file
if [ ! -f "/config.yaml" ]; then
    cp config.yaml /config.yaml
    echo "Configuration file installed to /config.yaml"
else
    echo "Configuration file already exists, not overwriting"
fi

# Install service
echo "Installing system service..."
cp autocopy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable autocopy.service

echo "Start service now? (y/n)"
read -r answer
if [ "$answer" = "y" ] || [ "$answer" = "Y" ]; then
    systemctl start autocopy.service
    echo "Service started"
else
    echo "Service not started, you can start it later with 'systemctl start autocopy.service'"
fi

echo "Installation complete!"
echo "Service status can be checked with 'systemctl status autocopy.service'"
echo "Log file located at /var/log/autocopy.log" 