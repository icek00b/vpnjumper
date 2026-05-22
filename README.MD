# VPN Jumper

A lightweight VPN region rotator that rebuilds the gluetun container to cycle through Private Internet Access (PIA) server regions. Uses gluetun's built-in HTTP proxy in stealth mode - no secondary container needed.

## Features

- **Randomized rotation**: Shuffles all available PIA regions on each run for maximum diversity
- **Stealth HTTP proxy**: Built-in gluetun proxy on port `4231` with no authentication headers
- **Two execution modes**: Docker SDK (preferred) or subprocess CLI fallback
- **Watch mode**: Continuous rotation at configurable intervals (default: 5 minutes)
- **Auto-rotation**: If proxy connection fails, automatically rotates to next random region
- **External credentials**: VPN username/password loaded from `config.txt` - no hardcoded secrets

## How It Works

VPN Jumper automates VPN region rotation by:

1. **Loading credentials** from `/config/vpnjumper/config.txt`
2. **Reading available regions** from gluetun's `servers.json` file
3. **Shuffling regions** randomly for unpredictable rotation order
4. **Stopping the old gluetun container** cleanly
5. **Starting a new gluetun container** with a random region
6. **Waiting for VPN connection** to establish (checks logs for successful connection)
7. **Auto-retry on failure**: If proxy is unreachable, immediately rotates again

The HTTP proxy runs in stealth mode - it doesn't add proxy headers to outgoing requests, making it harder for websites to detect you're using a proxy.

## Requirements

- Docker daemon running with accessible socket
- Python 3.7+
- Network access to `/dev/net/tun` device
- Valid PIA (Private Internet Access) VPN account
- `servers.json` file from gluetun in `/config/vpnjumper/gluetun/servers.json`

## Installation

```bash
# Install Python dependencies
pip install docker requests

# Create the config directory
mkdir -p /config/vpnjumper /config/vpnjumper/gluetun

# Make the script executable
chmod +x /config/vpnjumper/vpnjumper.py
```

## Configuration

### config.txt

Create `/config/vpnjumper/config.txt` with your PIA credentials:

```python
VPN_USER = "your_username"
VPN_PASS = "your_password"
```

The script loads these variables using Python's `exec()`, so follow Python string syntax (quotes around values).

**Security tip:** Protect the config file:
```bash
chmod 600 /config/vpnjumper/config.txt
```

## File Locations

| File | Purpose |
|------|---------|
| `/config/vpnjumper/vpnjumper.py` | Main script |
| `/config/vpnjumper/config.txt` | VPN credentials (user-editable, chmod 600 recommended) |
| `/config/vpnjumper/gluetun/servers.json` | PIA server list (from gluetun) |
| `/config/vpnjumper/gluetun/` | Gluetun volume for VPN config persistence |

## Usage

### Single Rotation

Rotate to a random region once:

```bash
# Using Docker SDK (preferred - better error handling)
python3 /config/vpnjumper/vpnjumper.py

# Using docker CLI subprocess (fallback)
python3 /config/vpnjumper/vpnjumper.py --cli
```

### Watch Mode (Continuous Rotation)

Automatically rotate at regular intervals:

```bash
# Rotate every 5 minutes (default)
python3 /config/vpnjumper/vpnjumper.py --watch

# Rotate every 10 minutes
python3 /config/vpnjumper/vpnjumper.py --watch --interval 600

# Rotate every 30 seconds (testing)
python3 /config/vpnjumper/vpnjumper.py --watch --interval 30
```

Watch mode handles failures gracefully - if a rotation fails, it waits 60 seconds and retries.

### List Available Regions

See all available regions:

```bash
python3 /config/vpnjumper/vpnjumper.py --list
```

## HTTP Proxy Usage

After rotation completes, the HTTP proxy is available at `http://localhost:4231` in stealth mode:

```bash
# Test the proxy - shows your VPN IP
curl -x http://localhost:4231 https://api.ipify.org

# Use with curl
curl -x http://localhost:4231 https://example.com

# Set environment variables for all requests
export HTTP_PROXY=http://localhost:4231
export HTTPS_PROXY=http://localhost:4231

# Use with Python requests
python3 -c "import requests; print(requests.get('https://ifconfig.me', proxies={'http': 'http://localhost:4231'}).text)"
```

## Logging

Logs are output to stdout with timestamps:

```
2026-05-21 22:58:15 - INFO - VPN credentials loaded from config.txt
2026-05-21 22:58:15 - INFO - Loaded 97 unique regions from servers.json
2026-05-21 22:58:15 - INFO - Regions randomized, total: 97
2026-05-21 22:58:15 - INFO - Selected region: Netherlands
2026-05-21 22:58:18 - INFO - Gluetun container started: a1b2c3d4e5f6
2026-05-21 22:58:25 - INFO - Container gluetun is running with VPN connected
2026-05-21 22:58:25 - INFO - VPN rotation complete!
2026-05-21 22:58:25 - INFO - HTTP proxy available on port 4231 (gluetun built-in, stealth mode)
```

## Troubleshooting

### Config file not found

```
ERROR - Config file not found at /config/vpnjumper/config.txt
```

**Fix:** Create the config file with your credentials (see Configuration section).

### Docker SDK not available

```
Warning: docker SDK not installed, using subprocess fallback
```

**Fix:** Install the Docker Python SDK (recommended):
```bash
pip install docker
```

Or use CLI mode:
```bash
python3 vpnjumper.py --cli
```

### Container fails to start

Check container logs:
```bash
docker logs gluetun
```

Common issues:
- **Invalid credentials**: Check `config.txt` for typos
- **Region not found**: Run `--list` to see valid region names
- **TUN device missing**: See "Permission denied" below

### Permission denied for /dev/net/tun

Ensure TUN device exists:
```bash
ls -la /dev/net/tun

# If missing, create it:
sudo mkdir -p /dev/net
sudo mknod /dev/net/tun c 10 200
sudo chmod 666 /dev/net/tun
```

### Proxy connection fails

```
WARNING - Proxy error after 3 attempts - VPN proxy may not be ready
WARNING - Bad endpoint detected, triggering automatic rotation...
```

The script automatically rotates to a new region. This can happen if:
- VPN connection took longer than expected
- The selected region has connectivity issues
- Port 4231 is blocked by firewall

### servers.json not found

```
INFO - servers.json not found at /config/vpnjumper/gluetun/servers.json
```

**Fix:** Download the servers.json file:
```bash
curl -o /config/vpnjumper/gluetun/servers.json \
  https://raw.githubusercontent.com/qdm2/gluetun/master/servers.json
```

## Security Notes

- **Credentials in config.txt**: Contains plain-text PIA credentials. Protect it with `chmod 600`.
- **No proxy authentication**: HTTP proxy accepts any request from localhost (trusted network assumption). Don't expose port 4231 to untrusted networks.
- **Volume persistence**: Gluetun config stored in `/config/vpnjumper/gluetun/` for faster reconnects.
- **Stealth mode**: Proxy doesn't add `X-Forwarded-For` or other proxy headers, making detection harder.

## Advanced Usage

### Run as systemd service

Create `/etc/systemd/system/vpnjumper.service`:

```ini
[Unit]
Description=VPN Jumper - Automatic VPN Region Rotator
After=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /config/vpnjumper/vpnjumper.py --watch --interval 300
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl enable vpnjumper
sudo systemctl start vpnjumper