# AutoCopy - Raspberry Pi Automatic File Copy Tool

[中文介绍](./README-zh.md)

This is an automatic file copy tool designed for Raspberry Pi 5 Model B that copies files from a smaller capacity USB storage device to a larger one when two USB storage devices are connected.

Demo:

https://github.com/user-attachments/assets/09343e59-0526-4d52-899a-851b53486170

## Features

- Automatically detects inserted USB storage devices
- Automatically copies files from smaller capacity device to larger capacity device
- Automatically ejects devices after copying is complete
- Uses LED indicators for different statuses
- Displays progress bar during file copying
- Runs as a daemon process
- Can be registered as a system service to start with system boot

## LED Status Indicators

- Yellow LED slow blinking (2 seconds on, 2 seconds off): Program running normally, waiting for USB devices
- Yellow LED fast blinking (0.5 seconds on, 0.5 seconds off): Copying files in progress
- Green LED blinking (1 second on, 1 second off): Copy successful
- Red LED blinking: Error occurred
  - 1 second on, 1 second off, blinking twice then 3 seconds off: Insufficient space on target storage device
  - 1 second on, 1 second off, blinking three times then 3 seconds off: Folder index reached maximum value
  - 1 second on, 1 second off, blinking four times then 2 seconds off: File system not supported
  - 0.5 seconds on, 0.5 seconds off, blinking three times then 2 seconds off: Failed to eject all devices
  - 0.5 seconds on, 0.5 seconds off, blinking five times then 2 seconds off: Other errors

## Installation

1. Make sure the Raspberry Pi is connected to the internet
2. Download all files to the same directory
3. Run the installation script:
  ```bash
  sudo chmod +x install.sh
  sudo ./install.sh
  ```

## Update

1. Update program file:
  ```bash
  sudo cp autocopy.py /usr/local/bin/
  sudo chmod +x /usr/local/bin/autocopy.py
  ```

2. Restart service:
  ```bash
  sudo systemctl restart autocopy.service
  ```

## Configuration

The configuration file is located at `/config.yaml`, you can modify the following settings:

```yaml
# Maximum folder index, will not copy when reached
max_folder_index: 999

# Whether to enable LED indicator function
led_enabled: true

# Check if target device has enough free space before copying
check_free_space: true

# Automatically eject devices after copying
auto_eject: true

# Maximum retry attempts for ejecting devices
eject_retry: 3

# Interval between retry attempts (seconds)
eject_retry_interval: 5
```

## Manual Running

If you don't want to install as a service, you can run it directly:

```bash
sudo python3 autocopy.py
```

Run as daemon process:

```bash
sudo python3 autocopy.py --daemon
```

## Logs

Log file located at: `/var/log/autocopy.log`

## Dependencies

- Python 3
- udisks2

Python requirements can be found in requirements.txt.

## Notes

- The program needs root privileges to control LEDs and access USB devices
- The copied folder name format is "YYYYMMDDNNN", e.g. "20250503001"
- When the folder index for the same day reaches 999, no more copying will be performed
- Due to the naming dependency of the target directory on the date, add the clock module battery to the Raspberry Pi if you need to copy it offline
- The author only has a Raspberry Pi 5 Model B, and the script is written for Raspberry Pi OS. Make proper changes if you need to run it on other Raspberry Pi or other Linux devices
