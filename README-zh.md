# AutoCopy - 树莓派自动文件复制工具

[English](./README.md)

这是一个为树莓派 5B 设计的自动文件复制工具，当插入两个 USB 存储设备时，自动将小容量设备的文件复制到大容量设备中。

## 功能特点

- 自动检测插入的 USB 存储设备
- 自动将小容量设备的文件复制到大容量设备
- 复制完成后自动弹出设备
- 通过 LED 灯指示不同的状态
- 复制文件时显示进度条
- 作为守护进程运行
- 可注册为系统服务，随系统启动

## LED状态指示

- 黄灯慢闪（亮 2 秒，灭 2 秒）：程序正常运行，等待USB设备
- 黄灯快闪（亮 0.5 秒，灭 0.5 秒）：正在复制文件
- 绿灯闪烁（亮 1 秒，灭 1 秒）：复制成功
- 红灯闪烁：出现错误
  - 亮 1 秒，灭 1 秒，亮两次后灭 3 秒：目标存储设备空间不足
  - 亮 1 秒，灭 1 秒，亮三次后灭 3 秒：文件夹序号达到最大值
  - 亮 1 秒，灭 1 秒，亮四次后灭 2 秒：文件系统不支持
  - 亮 0.5 秒，灭 0.5 秒，亮三次后灭 2 秒：未能弹出全部设备
  - 亮 0.5 秒，灭 0.5 秒，亮五次后灭 2 秒：其他错误

## 安装方法

1. 确保树莓派已连接到互联网
2. 下载所有文件到同一目录
3. 运行安装脚本：
  ```bash
  sudo chmod +x install.sh
  sudo ./install.sh
  ```

## 更新

1. 更新程序文件：
  ```bash
  sudo cp autocopy.py /usr/local/bin/
  sudo chmod +x /usr/local/bin/autocopy.py
  ```

2. 重启服务：
  ```bash
  sudo systemctl restart autocopy.service
  ```

## 配置

配置文件为`/config.yaml`，可以修改以下设置：

```yaml
# 文件夹序号最大值，达到此值将不再复制
max_folder_index: 999

# 是否启用 LED 指示灯功能
led_enabled: true

# 复制前检查目标设备剩余空间是否足够 
check_free_space: true

# 复制完成后自动弹出设备
auto_eject: true

# 弹出设备最大重试次数
eject_retry: 3

# 弹出设备重试间隔（秒）
eject_retry_interval: 5
```

## 手动运行

如果不想安装为服务，也可以直接运行：

```bash
sudo python3 autocopy.py
```

以守护进程方式运行：

```bash
sudo python3 autocopy.py --daemon
```

## 日志

日志文件位于：`/var/log/autocopy.log`

## 依赖

- Python 3
- udisks2

Python 依赖项见 requirements.txt。

## 注意事项

- 程序需要 root 权限才能操作 LED 和访问 USB 设备
- 复制的文件夹名称格式为"YYYYMMDDNNN"，如"20250503001"
- 当同一天的序号达到 999 时，不会再进行复制
- 由于目标目录的命名依赖日期，如果需要离线复制，请给树莓派加上时钟模块电池
- 作者手中只有树莓派 5B，脚本按照 Raspberry Pi OS 编写。如果需要用到别的树莓派或其他 Linux 设备，请自行做更改