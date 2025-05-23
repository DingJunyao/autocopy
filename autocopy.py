#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import logging
import signal
import re
import yaml
import shutil
import subprocess
from datetime import datetime
from threading import Thread, Event
import pyudev
import psutil
from tqdm import tqdm

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("/var/log/autocopy.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('autocopy')

# LED control
PWR_LED = "/sys/class/leds/PWR/brightness"
ACT_LED = "/sys/class/leds/ACT/brightness"

# LED states
LED_OFF = (0, 1)  # Off
LED_GREEN = (0, 0)  # Green light
LED_YELLOW = (1, 0)  # Yellow light
LED_RED = (1, 1)  # Red light

# Error codes
ERROR_NONE = 0
ERROR_SPACE_NOT_ENOUGH = 1
ERROR_MAX_FOLDER_INDEX = 2
ERROR_FILESYSTEM = 3
ERROR_EJECT_FAILED = 4
ERROR_OTHER = 5

class AutoCopy:
    def __init__(self):
        self.config = self._load_config()
        self.stop_event = Event()
        self.current_led_thread = None
        self.context = pyudev.Context()
        self.monitor = pyudev.Monitor.from_netlink(self.context)
        self.monitor.filter_by(subsystem='block', device_type='partition')
        
        # Status tracking
        self.usb_devices = {}  # Currently connected USB devices
        self.copy_in_progress = False
        self.last_task_complete_time = time.time()  # Last task completion time
        self.log_device_scan = True  # Whether to log device scanning
        
        # Signal handling
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _load_config(self):
        """Load configuration file"""
        config_path = "/config.yaml"
        default_config = {
            "max_folder_index": 999,
            "led_enabled": True,
        }
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = yaml.safe_load(f)
                    if config:
                        default_config.update(config)
            except Exception as e:
                logger.error(f"Failed to load configuration file: {e}")
        
        return default_config
    
    def _set_led(self, pwr_state, act_state):
        """Set LED state"""
        try:
            with open(PWR_LED, 'w') as f:
                f.write(str(pwr_state))
            with open(ACT_LED, 'w') as f:
                f.write(str(act_state))
        except Exception as e:
            logger.error(f"Failed to set LED: {e}")
    
    def _led_pattern(self, led_color, on_time, off_time, pattern=None, stop_event=None):
        """Control LED blinking pattern
        
        Args:
            led_color: (pwr, act) tuple
            on_time: LED on time (seconds)
            off_time: LED off time (seconds)
            pattern: Special pattern, e.g. [2, 3] means blink twice then off for 3 seconds
            stop_event: Stop event
        """
        if stop_event is None:
            stop_event = self.stop_event
            
        logger.info(f"Starting LED blinking pattern: color={led_color}, on={on_time}s, off={off_time}s, pattern={pattern}")
            
        while not stop_event.is_set():
            if pattern:
                for _ in range(pattern[0]):
                    if stop_event.is_set():
                        break
                    self._set_led(*led_color)
                    if not stop_event.wait(on_time):  # If wait returns False, it means timeout rather than event being set
                        self._set_led(*LED_OFF)
                        stop_event.wait(off_time)
                if not stop_event.is_set():
                    stop_event.wait(pattern[1])
            else:
                self._set_led(*led_color)
                if not stop_event.wait(on_time):  # If wait returns False, it means timeout rather than event being set
                    self._set_led(*LED_OFF)
                    stop_event.wait(off_time)
        
        logger.info(f"LED blinking pattern ended: color={led_color}")
        # Ensure LED is off when exiting
        self._set_led(*LED_OFF)
    
    def _start_led_pattern(self, pattern_type, error_code=ERROR_NONE):
        """Start specific LED pattern, stop current pattern"""
        logger.info(f"Setting LED mode: {pattern_type}")
        
        if self.current_led_thread and self.current_led_thread.is_alive():
            logger.info("Stopping current LED thread")
            self.current_led_thread.stop_event.set()
            self.current_led_thread.join(1)
        
        thread_stop_event = Event()
        if pattern_type == "idle":
            # Yellow LED slow blink (on 2s, off 2s)
            thread = Thread(target=self._led_pattern, args=(LED_YELLOW, 2, 2), kwargs={"stop_event": thread_stop_event})
            logger.info("Setting yellow LED slow blink")
        elif pattern_type == "copying":
            # Yellow LED fast blink (on 0.5s, off 0.5s)
            thread = Thread(target=self._led_pattern, args=(LED_YELLOW, 0.5, 0.5), kwargs={"stop_event": thread_stop_event})
            logger.info("Setting yellow LED fast blink")
        elif pattern_type == "success":
            # Green LED blink (on 1s, off 1s)
            thread = Thread(target=self._led_pattern, args=(LED_GREEN, 1, 1), kwargs={"stop_event": thread_stop_event})
            logger.info("Setting green LED blink")
        elif pattern_type == "error":
            # Error patterns
            if error_code == ERROR_SPACE_NOT_ENOUGH:
                # Insufficient space: red LED on 1s, off 1s, blink twice then off 3s
                thread = Thread(target=self._led_pattern, args=(LED_RED, 1, 1, [2, 3]), kwargs={"stop_event": thread_stop_event})
                logger.info("Setting red LED error mode: insufficient space")
            elif error_code == ERROR_MAX_FOLDER_INDEX:
                # Folder index reached max: red LED on 1s, off 1s, blink three times then off 3s
                thread = Thread(target=self._led_pattern, args=(LED_RED, 1, 1, [3, 3]), kwargs={"stop_event": thread_stop_event})
                logger.info("Setting red LED error mode: max folder index")
            elif error_code == ERROR_FILESYSTEM:
                # Filesystem not supported: red LED on 1s, off 1s, blink four times then off 2s
                thread = Thread(target=self._led_pattern, args=(LED_RED, 1, 1, [4, 2]), kwargs={"stop_event": thread_stop_event})
                logger.info("Setting red LED error mode: filesystem")
            elif error_code == ERROR_EJECT_FAILED:
                # Failed to eject devices: red LED on 0.5s, off 0.5s, blink three times then off 2s
                thread = Thread(target=self._led_pattern, args=(LED_RED, 0.5, 0.5, [3, 2]), kwargs={"stop_event": thread_stop_event})
                logger.info("Setting red LED error mode: eject failed")
            else:
                # Other errors: red LED on 0.5s, off 0.5s, blink five times then off 2s
                thread = Thread(target=self._led_pattern, args=(LED_RED, 0.5, 0.5, [5, 2]), kwargs={"stop_event": thread_stop_event})
                logger.info("Setting red LED error mode: other error")
        else:
            logger.warning(f"Unknown LED mode: {pattern_type}")
            return
            
        thread.daemon = True
        thread.stop_event = thread_stop_event
        thread.start()
        self.current_led_thread = thread
        logger.info("LED thread started")
    
    def _get_folder_name(self, mount_point):
        """Generate target folder name"""
        today = datetime.now().strftime("%Y%m%d")
        index = 1
        
        # Find existing folders
        pattern = re.compile(r'^(\d{8})(\d{3})$')
        for item in os.listdir(mount_point):
            if os.path.isdir(os.path.join(mount_point, item)):
                match = pattern.match(item)
                if match and match.group(1) == today:
                    current_index = int(match.group(2))
                    if current_index >= index:
                        index = current_index + 1
        
        # Check if index exceeds maximum value
        if index > self.config["max_folder_index"]:
            logger.error(f"Folder index has reached maximum value: {self.config['max_folder_index']}")
            return None
            
        return f"{today}{index:03d}"
    
    def _get_usb_storage_devices(self):
        """Get currently connected USB storage devices"""
        # Check if stop signal has been received
        if self.stop_event.is_set():
            logger.info("Stop signal detected, aborting device detection")
            return {}
            
        devices = {}
        physical_devices = set()  # For tracking physical devices to prevent duplicate counting
        
        # First try to identify partitions
        for device in self.context.list_devices(subsystem='block', DEVTYPE='partition'):
            # Modified condition to support both USB devices and ATA devices connected via USB
            is_usb = (device.get('ID_BUS') == 'usb' or 
                     (device.get('ID_BUS') == 'ata' and device.get('ID_USB_DRIVER') == 'usb-storage'))
            
            if is_usb:
                try:
                    # Find mount point
                    mount_point = None
                    for part in psutil.disk_partitions():
                        if part.device == device.device_node:
                            mount_point = part.mountpoint
                            break
                    
                    if mount_point:
                        # Identify physical device
                        if device.parent:
                            physical_device = device.parent.device_node
                        else:
                            physical_device = device.device_node.rstrip('0123456789')
                        
                        # Skip if the physical device has already been recorded
                        if physical_device in physical_devices:
                            if self.log_device_scan:  # Only log when needed
                                logger.info(f"Skipping partition {device.device_node}, its physical device {physical_device} is already recorded")
                            continue
                            
                        # Record this physical device
                        physical_devices.add(physical_device)
                        
                        # Get total capacity
                        total_size = shutil.disk_usage(mount_point).total
                        devices[device.device_node] = {
                            'mount_point': mount_point,
                            'size': total_size,
                            'device': device,
                            'physical_device': physical_device
                        }
                        if self.log_device_scan:  # Only log when needed
                            logger.info(f"Found USB partition: {device.device_node} mounted at {mount_point}, physical device: {physical_device}")
                except Exception as e:
                    logger.error(f"Failed to get device information {device.device_node}: {e}")
        
        # If not enough partition devices found, try to identify entire disk devices
        if len(physical_devices) < 2:
            # Only log when needed
            if self.log_device_scan:
                logger.info("Insufficient physical devices, trying to identify entire disk devices")
                
            for device in self.context.list_devices(subsystem='block', DEVTYPE='disk'):
                is_usb = (device.get('ID_BUS') == 'usb' or 
                         (device.get('ID_BUS') == 'ata' and device.get('ID_USB_DRIVER') == 'usb-storage'))
                
                # Skip if physical device already recorded
                if device.device_node in physical_devices:
                    if self.log_device_scan:  # Only log when needed
                        logger.info(f"Skipping disk {device.device_node}, already recorded as physical device")
                    continue
                    
                if is_usb and device.device_node not in devices:
                    # Check if this disk has any partitions that are mounted
                    has_mounted_partition = False
                    for part_device in self.context.list_devices(subsystem='block', DEVTYPE='partition'):
                        # Check if this partition belongs to the current disk
                        if part_device.parent == device:
                            for disk_part in psutil.disk_partitions():
                                if disk_part.device == part_device.device_node:
                                    # Found a mounted partition of the disk
                                    
                                    # Skip if this disk is already recorded as physical device
                                    if device.device_node in physical_devices:
                                        if self.log_device_scan:  # Only log when needed
                                            logger.info(f"Skipping disk {device.device_node}, already recorded as physical device")
                                        continue
                                    
                                    # Record this physical device
                                    physical_devices.add(device.device_node)
                                    
                                    mount_point = disk_part.mountpoint
                                    total_size = shutil.disk_usage(mount_point).total
                                    devices[device.device_node] = {
                                        'mount_point': mount_point,
                                        'size': total_size,
                                        'device': device,
                                        'partition': part_device.device_node,
                                        'physical_device': device.device_node
                                    }
                                    if self.log_device_scan:  # Only log when needed
                                        logger.info(f"Found USB disk: {device.device_node} through partition {part_device.device_node} mounted at {mount_point}")
                                    has_mounted_partition = True
                                    break
                            if has_mounted_partition:
                                break
                    
                    if not has_mounted_partition:
                        if self.log_device_scan:  # Only log when needed
                            logger.info(f"Found unmounted USB disk: {device.device_node}, attempting to mount its partitions")
        
        # Not enough devices, try to check if there are unmounted partitions
        if len(physical_devices) < 2 and not self.stop_event.is_set():
            # Only log when needed
            if self.log_device_scan:
                logger.info(f"Insufficient physical devices detected ({len(physical_devices)}), checking for unmounted partitions")
                
            self._check_unmounted_partitions()
            
            # Check if stop signal was received during the check
            if self.stop_event.is_set():
                logger.info("Stop signal detected, aborting device detection")
                return {}
                
            # Try to get devices again
            time.sleep(1)  # Wait for mounting to complete
            
            # Don't repeat these logs in recursive call
            old_log_setting = self.log_device_scan
            self.log_device_scan = False  # Temporarily disable logging
            result = self._get_usb_storage_devices()
            self.log_device_scan = old_log_setting  # Restore logging setting
            return result
            
        return devices
    
    def _check_unmounted_partitions(self):
        """Check unmounted partitions and attempt to mount them"""
        # Check if stop signal has been received
        if self.stop_event.is_set():
            logger.info("Stop signal detected, aborting partition check")
            return
        
        try:
            # Find possible USB storage devices
            for device in self.context.list_devices(subsystem='block', DEVTYPE='partition'):
                # Check if it's a USB device or ATA device connected via USB
                is_usb = (device.get('ID_BUS') == 'usb' or 
                         (device.get('ID_BUS') == 'ata' and device.get('ID_USB_DRIVER') == 'usb-storage'))
                
                # Output device info for debugging
                if is_usb and self.log_device_scan:  # Only log when needed
                    logger.info(f"Found USB-related device: {device.device_node}, ID_BUS={device.get('ID_BUS')}, "
                               f"ID_USB_DRIVER={device.get('ID_USB_DRIVER')}, "
                               f"ID_FS_USAGE={device.get('ID_FS_USAGE')}")
                
                # Skip already mounted devices
                is_mounted = False
                for part in psutil.disk_partitions():
                    if part.device == device.device_node:
                        is_mounted = True
                        break
                
                # If it's a USB device and not mounted, try to mount it
                if is_usb and not is_mounted:
                    logger.info(f"Attempting to mount partition: {device.device_node}")
                    try:
                        # Use udisks2 to mount
                        cmd = ["udisksctl", "mount", "-b", device.device_node]
                        subprocess.run(cmd, check=True)
                        logger.info(f"Successfully mounted partition: {device.device_node}")
                    except Exception as e:
                        logger.error(f"Failed to mount partition {device.device_node}: {e}")
        except Exception as e:
            logger.error(f"Error checking unmounted partitions: {e}")
    
    def _eject_device(self, device_path):
        """Eject USB device"""
        try:
            # Check if device exists or is accessible
            device_exists = os.path.exists(device_path)
            if not device_exists:
                logger.info(f"Device {device_path} no longer exists, may have been ejected already")
                return True
            
            # Check if it's an entire disk
            is_disk = 'partition' not in device_path[-1] and device_path[-1].isdigit() == False
            
            logger.info(f"Preparing to eject device: {device_path}, is entire disk: {is_disk}")
            
            # If it's a disk, eject all its partitions first
            if is_disk:
                # Find all partitions belonging to the disk
                for device in self.context.list_devices(subsystem='block', DEVTYPE='partition'):
                    parent_device = device.parent.device_node if device.parent else None
                    if parent_device == device_path:
                        mount_point = None
                        for part in psutil.disk_partitions():
                            if part.device == device.device_node:
                                mount_point = part.mountpoint
                                break
                        
                        if mount_point:
                            logger.info(f"Ejecting disk {device_path} partition {device.device_node}")
                            try:
                                # Use udisks2 to unmount partition
                                cmd = ["udisksctl", "unmount", "-b", device.device_node]
                                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                                if result.returncode == 0:
                                    logger.info(f"Partition {device.device_node} successfully unmounted")
                                else:
                                    # Check if it's because the device is not mounted
                                    if "not mounted" in result.stderr:
                                        logger.info(f"Partition {device.device_node} is already unmounted")
                                    else:
                                        logger.error(f"Failed to unmount partition {device.device_node}: {result.stderr}")
                                        return False
                            except Exception as e:
                                logger.error(f"Failed to unmount partition {device.device_node}: {e}")
                                return False
            else:
                # Directly unmount partition
                try:
                    cmd = ["udisksctl", "unmount", "-b", device_path]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                    if result.returncode == 0:
                        logger.info(f"Device {device_path} successfully unmounted")
                    else:
                        # Check if it's because the device is not mounted
                        if "not mounted" in result.stderr:
                            logger.info(f"Device {device_path} is already unmounted")
                        else:
                            logger.error(f"Failed to unmount device {device_path}: {result.stderr}")
                            # But continue to try executing power-off operation
                except Exception as e:
                    logger.error(f"Failed to unmount device {device_path}: {e}")
                    # But still continue to try executing power-off operation
            
            # For ATA devices connected via USB, special handling may be needed
            dev_info = None
            for device in self.context.list_devices(subsystem='block'):
                if device.device_node == device_path:
                    dev_info = device
                    break
            
            # Get parent device (entire USB drive)
            if is_disk:
                # If it's already an entire disk, try to get USB parent device
                try:
                    # Check if it's an ATA device
                    if dev_info and dev_info.get('ID_BUS') == 'ata':
                        logger.info(f"Detected ATA device, trying to find USB parent device")
                        # Traverse device tree to find USB parent device
                        current_dev = dev_info
                        while current_dev:
                            if current_dev.get('ID_USB_DRIVER') == 'usb-storage':
                                # Found USB storage device
                                parent_device = current_dev.device_node
                                break
                            current_dev = current_dev.parent
                    else:
                        parent_device = device_path
                except Exception as e:
                    logger.error(f"Failed to find USB parent device: {e}")
                    parent_device = device_path
            else:
                # If it's a partition, get its disk device
                parent_device = device_path.rstrip('0123456789')
            
            # Check if parent device exists
            if not os.path.exists(parent_device):
                logger.info(f"Parent device {parent_device} no longer exists, may have been ejected already")
                return True
                
            logger.info(f"Attempting to power off: {parent_device}")
            try:
                cmd = ["udisksctl", "power-off", "-b", parent_device]
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if result.returncode == 0:
                    logger.info(f"Device {parent_device} successfully powered off")
                    return True
                else:
                    # Check error message, some errors may indicate device has been ejected
                    if "looking up object" in result.stderr:
                        logger.info(f"Device {parent_device} no longer exists, may have been ejected already")
                        return True
                    else:
                        logger.error(f"Failed to power off device {parent_device}: {result.stderr}")
                        return False
            except Exception as e:
                logger.error(f"Failed to power off device {parent_device}: {e}")
                return False
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to eject device {device_path}: {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to eject device {device_path}: {e}")
            return False
    
    def _copy_files(self, source_path, target_path):
        """
        Copy files with progress display
        
        Args:
            source_path: Source directory
            target_path: Target directory
        
        Returns:
            (bool, error_code): True for success, error code
        """
        try:
            # Check target disk space
            source_size = sum(os.path.getsize(os.path.join(root, file)) 
                             for root, _, files in os.walk(source_path) 
                             for file in files)
            
            target_free = shutil.disk_usage(os.path.dirname(target_path)).free
            
            if source_size > target_free:
                logger.error(f"Insufficient space on target device. Required: {source_size/1024/1024:.1f}MB, Available: {target_free/1024/1024:.1f}MB")
                return False, ERROR_SPACE_NOT_ENOUGH
            
            # Create target directory
            os.makedirs(target_path, exist_ok=True)
            
            # Get all files
            all_files = []
            for root, _, files in os.walk(source_path):
                for file in files:
                    src_file = os.path.join(root, file)
                    # Calculate relative path
                    rel_path = os.path.relpath(src_file, source_path)
                    dest_file = os.path.join(target_path, rel_path)
                    all_files.append((src_file, dest_file, os.path.getsize(src_file)))
            
            # Copy files
            total_files = len(all_files)
            total_size = sum(f[2] for f in all_files)
            
            with tqdm(total=total_size, unit='B', unit_scale=True) as pbar:
                for i, (src_file, dest_file, size) in enumerate(all_files):
                    # Create directory for destination file
                    os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                    
                    logger.info(f"Copying file [{i+1}/{total_files}]: {os.path.basename(src_file)}")
                    pbar.set_description(f"Copying {os.path.basename(src_file)}")
                    
                    # Use buffered copy to show progress
                    with open(src_file, 'rb') as fsrc, open(dest_file, 'wb') as fdst:
                        copied = 0
                        while True:
                            buf = fsrc.read(1024*1024)  # 1MB buffer
                            if not buf:
                                break
                            fdst.write(buf)
                            copied += len(buf)
                            pbar.update(len(buf))
            
            return True, ERROR_NONE
            
        except PermissionError:
            logger.error("Permission error copying files")
            return False, ERROR_FILESYSTEM
        except Exception as e:
            logger.error(f"Error copying files: {e}")
            return False, ERROR_OTHER
    
    def _process_devices(self):
        """Process file copying between two USB devices"""
        # Check if stop signal has been received
        if self.stop_event.is_set():
            logger.info("Stop signal detected, aborting device processing")
            return
            
        if self.copy_in_progress:
            return
            
        # Get currently connected USB devices
        devices = self._get_usb_storage_devices()
        
        # Determine number of detected physical devices
        physical_devices = set()
        for dev_path, dev_info in devices.items():
            if 'physical_device' in dev_info:
                physical_devices.add(dev_info['physical_device'])
        
        # Output information about detected devices
        if len(devices) > 0:
            logger.info(f"Currently detected {len(devices)} USB storage devices, {len(physical_devices)} physical devices")
            for dev_path, dev_info in devices.items():
                logger.info(f"Device: {dev_path}, Mount point: {dev_info['mount_point']}, Size: {dev_info['size']/1024/1024/1024:.1f}GB, Physical device: {dev_info.get('physical_device', 'Unknown')}")
        
        # Check if there are enough different physical devices
        if len(physical_devices) < 2:
            # Don't output this log on every poll
            # Only output on first poll shortly after a task completes
            current_time = time.time()
            if current_time - self.last_task_complete_time < 5:
                # Only output log within 5 seconds after task completion
                logger.info(f"Insufficient physical devices ({len(physical_devices)}), need 2 different physical devices to start copying")
                # Reset flag to avoid repeated output
                self.last_task_complete_time = 0
            return
            
        # If there are two devices, perform copying
        if len(devices) >= 2:
            self.copy_in_progress = True
            self._start_led_pattern("copying")
            
            try:
                # Sort by capacity
                sorted_devices = sorted(devices.items(), key=lambda x: x[1]['size'])
                small_device = sorted_devices[0]
                large_device = sorted_devices[1]
                
                # Check if small device and large device are the same physical device
                small_physical = small_device[1].get('physical_device', small_device[0])
                large_physical = large_device[1].get('physical_device', large_device[0])
                
                if small_physical == large_physical:
                    logger.error(f"Detected same physical device, cannot perform copying: {small_physical}")
                    self._start_led_pattern("error", ERROR_OTHER)
                    self.copy_in_progress = False
                    return
                
                small_path = small_device[1]['mount_point']
                large_path = large_device[1]['mount_point']
                
                # Check if mount points are the same
                if small_path == large_path:
                    logger.error(f"Two devices have the same mount point, cannot perform copying: {small_path}")
                    self._start_led_pattern("error", ERROR_OTHER)
                    self.copy_in_progress = False
                    return
                
                logger.info(f"Found two USB devices:")
                logger.info(f"Small capacity device: {small_device[0]} ({small_path}), Size: {small_device[1]['size']/1024/1024/1024:.1f}GB")
                logger.info(f"Large capacity device: {large_device[0]} ({large_path}), Size: {large_device[1]['size']/1024/1024/1024:.1f}GB")
                
                # Generate target folder name
                folder_name = self._get_folder_name(large_path)
                if not folder_name:
                    self._start_led_pattern("error", ERROR_MAX_FOLDER_INDEX)
                    self.copy_in_progress = False
                    return
                    
                target_folder = os.path.join(large_path, folder_name)
                
                # Execute copying
                logger.info(f"Starting to copy files from {small_path} to {target_folder}")
                success, error_code = self._copy_files(small_path, target_folder)
                
                # Collect devices to eject, regardless of copying success
                devices_to_eject = []
                
                # Copy failed but error is eject related, do not eject
                skip_eject = False
                if not success and error_code == ERROR_EJECT_FAILED:
                    skip_eject = True
                
                if not skip_eject:
                    # Collect devices to eject
                    if 'partition' in small_device[1]:
                        # If recorded partition, use partition instead of disk device
                        devices_to_eject.append(small_device[1]['partition'])
                    else:
                        devices_to_eject.append(small_device[0])
                        
                    if 'partition' in large_device[1]:
                        devices_to_eject.append(large_device[1]['partition'])
                    else:
                        devices_to_eject.append(large_device[0])
                    
                    # Remove duplicates to avoid repeated ejects
                    devices_to_eject = list(set(devices_to_eject))
                    logger.info(f"Devices to eject: {devices_to_eject}")
                
                if success:
                    logger.info("Files copied successfully")
                    # Do not set success LED yet, wait for eject before setting final state
                    
                    # Eject devices
                    eject_failures = 0
                    
                    for device in devices_to_eject:
                        for attempt in range(3):
                            logger.info(f"Attempting to eject device {device}, attempt {attempt+1}")
                            if self._eject_device(device):
                                logger.info(f"Device {device} ejected successfully")
                                break
                            else:
                                logger.warning(f"Failed to eject device {device}, waiting 5 seconds before retrying")
                                time.sleep(5)
                                if attempt == 2:  # Last attempt
                                    eject_failures += 1
                    
                    # Eject logic completed, whether successful or failed reset flag and state
                    if eject_failures > 0:
                        logger.error(f"{eject_failures} devices failed to eject")
                        self._start_led_pattern("error", ERROR_EJECT_FAILED)
                    else:
                        logger.info("All devices ejected successfully, set success LED")
                        
                        # Ensure current LED thread is terminated
                        if self.current_led_thread and self.current_led_thread.is_alive():
                            logger.info("Terminating current LED thread")
                            self.current_led_thread.stop_event.set()
                            self.current_led_thread.join(2)  # Give enough time to ensure thread termination
                            
                            # If thread is still alive, try stronger termination method
                            if self.current_led_thread.is_alive():
                                logger.warning("LED thread did not terminate normally, trying to force LED state")
                                self._set_led(*LED_OFF)  # Turn off all LEDs
                                time.sleep(0.5)
                        
                        # Set success LED state
                        logger.info(f"Setting success LED mode: Green LED flashing {LED_GREEN}")
                        
                        # Directly create new thread, do not use _start_led_pattern
                        success_stop_event = Event()
                        success_thread = Thread(
                            target=self._led_pattern, 
                            args=(LED_GREEN, 1, 1),
                            kwargs={"stop_event": success_stop_event}
                        )
                        success_thread.daemon = True
                        success_thread.stop_event = success_stop_event
                        
                        # Nullify reference to old thread
                        self.current_led_thread = None
                        
                        # Start new thread
                        success_thread.start()
                        self.current_led_thread = success_thread
                        logger.info("Success LED thread started")
                    
                    # Mark task completion time
                    self.last_task_complete_time = time.time()
                    self.log_device_scan = True  # Restore logging
                else:
                    logger.error(f"Copying files failed, error code: {error_code}")
                    
                    if skip_eject:
                        # If eject related error, do not attempt eject, set error LED directly
                        self._start_led_pattern("error", error_code)
                    else:
                        # Attempt eject
                        logger.info("Even if copying failed, still attempt to eject devices")
                        eject_failures = 0
                        
                        for device in devices_to_eject:
                            for attempt in range(3):
                                logger.info(f"Attempting to eject device {device}, attempt {attempt+1}")
                                if self._eject_device(device):
                                    logger.info(f"Device {device} ejected successfully")
                                    break
                                else:
                                    logger.warning(f"Failed to eject device {device}, waiting 5 seconds before retrying")
                                    time.sleep(5)
                                    if attempt == 2:  # Last attempt
                                        eject_failures += 1
                        
                        # Eject completed, show original error
                        logger.info(f"Completed device eject attempt, returning to original error handling, error code: {error_code}")
                        self._start_led_pattern("error", error_code)
            
            except Exception as e:
                logger.error(f"Error processing devices: {e}")
                self._start_led_pattern("error", ERROR_OTHER)
            
            finally:
                self.copy_in_progress = False
        else:
            # If device count is not 2, reset to idle state
            if not self.copy_in_progress and (self.current_led_thread is None or 
                                           not self.current_led_thread.is_alive() or 
                                           self.current_led_thread.stop_event.is_set()):
                self._start_led_pattern("idle")
    
    def _handle_signal(self, signum, frame):
        """Handle termination signal"""
        # Avoid repeated processing
        if self.stop_event.is_set():
            return

        logger.info(f"Received signal {signum}, preparing to exit")
        # Set stop event
        self.stop_event.set()
        
        # Terminate all threads
        if self.current_led_thread and self.current_led_thread.is_alive():
            self.current_led_thread.stop_event.set()
            self.current_led_thread.join(0.5)
            
        # Ensure LEDs are off
        try:
            self._set_led(*LED_OFF)
        except Exception as e:
            logger.error(f"Error turning off LEDs: {e}")
        
        logger.info("Exiting program")
        # Use os._exit to force exit, do not execute cleanup handlers
        os._exit(0)
    
    def run(self):
        """Main loop"""
        # Set stronger signal handling
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)
        
        logger.info("AutoCopy service started")
        
        # Record run environment
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Configuration: {self.config}")
        logger.info(f"LED configuration: OFF={LED_OFF}, GREEN={LED_GREEN}, YELLOW={LED_YELLOW}, RED={LED_RED}")
        
        # Check LED files exist
        if os.path.exists(PWR_LED) and os.path.exists(ACT_LED):
            logger.info("LED control files exist, LED functionality available")
        else:
            logger.warning(f"LED control files do not exist! PWR_LED: {os.path.exists(PWR_LED)}, ACT_LED: {os.path.exists(ACT_LED)}")
        
        # Start LED state
        self._start_led_pattern("idle")
        
        # Get and record all block devices in system
        logger.info("Block devices in system:")
        for device in self.context.list_devices(subsystem='block'):
            dev_type = device.get('DEVTYPE', 'unknown')
            id_bus = device.get('ID_BUS', 'unknown')
            id_usb = device.get('ID_USB_DRIVER', 'none')
            logger.info(f"  - {device.device_node}: TYPE={dev_type}, BUS={id_bus}, USB_DRIVER={id_usb}")
        
        # Set device monitoring
        observer = pyudev.MonitorObserver(self.monitor, self._on_device_event)
        observer.daemon = True  # Ensure observer thread is daemon, exits with main thread
        observer.start()
        
        try:
            # Check existing devices
            self._process_devices()
            
            # Main loop
            while not self.stop_event.is_set():
                try:
                    # Use timeout wait to allow signal processing program to respond in a timely manner
                    time.sleep(0.5)
                except KeyboardInterrupt:
                    # Directly call signal processing function
                    self._handle_signal(signal.SIGINT, None)
                    break
                
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt, preparing to exit")
            self._handle_signal(signal.SIGINT, None)
        finally:
            # Ensure cleanup work
            if observer.is_alive():
                observer.stop()
            
            self.stop_event.set()
            
            # Ensure LEDs are off again
            if self.current_led_thread and self.current_led_thread.is_alive():
                self.current_led_thread.stop_event.set()
                self.current_led_thread.join(0.5)
            self._set_led(*LED_OFF)
    
    def _on_device_event(self, action, device):
        """USB device event processing"""
        # Modify condition to support both USB devices and ATA devices connected via USB
        is_usb = (device.get('ID_BUS') == 'usb' or 
                 (device.get('ID_BUS') == 'ata' and device.get('ID_USB_DRIVER') == 'usb-storage'))
        
        if action == 'add' and is_usb:
            logger.info(f"Detected new device: {device.device_node}")
            # Wait for device to mount
            time.sleep(2)
            self._process_devices()
        elif action == 'remove':
            logger.info(f"Device removed: {device.device_node}")
            # For remove event, we wait a little time for system to complete unmount
            time.sleep(1)
            self._process_devices()

def create_daemon():
    """Create daemon process"""
    try:
        # First fork
        pid = os.fork()
        if pid > 0:
            sys.exit(0)  # Parent process exits
    except OSError as e:
        logger.error(f"First fork failed: {e}")
        sys.exit(1)
    
    # Separate environment
    os.chdir('/')
    os.setsid()
    os.umask(0)
    
    try:
        # Second fork
        pid = os.fork()
        if pid > 0:
            sys.exit(0)  # First child process exits
    except OSError as e:
        logger.error(f"Second fork failed: {e}")
        sys.exit(1)
    
    # Redirect standard input only
    sys.stdout.flush()
    sys.stderr.flush()
    
    with open('/dev/null', 'r') as f:
        os.dup2(f.fileno(), sys.stdin.fileno())
    
    # Write PID file
    with open('/var/run/autocopy.pid', 'w') as f:
        f.write(str(os.getpid()))
    
    # Run main program
    autocopy = AutoCopy()
    autocopy.run()

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--daemon":
        create_daemon()
    else:
        # Run directly
        autocopy = AutoCopy()
        autocopy.run() 