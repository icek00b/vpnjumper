# VPN Jumper

A lightweight VPN region rotator that rebuilds the gluetun container to cycle through Private Internet Access (PIA) server regions. Uses gluetun's built-in HTTP proxy in stealth mode - no secondary container needed.

## Features

- **Auto-rotation**: Cycles through 97 PIA server regions sequentially
- **Stealth HTTP proxy**: Built-in gluetun proxy on port `4231` with no authentication headers
- **Two execution modes**: Docker SDK or subprocess fallback
- **Watch mode**: Continuous rotation every 5 minutes (configurable)
- **Persistent state**: Tracks current region via `/config/vpnjumper/region_index`
- **Zero config**: Works out of the box with hardcoded PIA credentials

## Requirements

- Docker daemon running with accessible socket
- Python 3.7+
- Network access to `/dev/net/tun` device
- Valid PIA credentials (hardcoded in script)

## Installation

```bash
# Install Python dependencies
pip install -r requirements.txt

# Optional: create the config directory
mkdir -p /config/vpnjumper /config/gluetun
```

## Usage

### Single Rotation
```bash
# Using Docker SDK (preferred)
python3 /config/vpnjumper/vpnjumper.py

# Using docker CLI subprocess
python3 /config/vpnjumper/vpnjumper.py --cli
```

### Watch Mode (Continuous Rotation)
```bash
# Rotate every 5 minutes (default)
python3 /config/vpnjumper/vpnjumper.py --watch

# Rotate every 10 minutes
python3 /config/vpnjumper/vpnjumper.py --watch --interval 600
```

### List Available Regions
```bash
python3 /config/vpnjumper/vpnjumper.py --list
```

## Configuration

### Environment Variables (Hardcoded)
- `VPN_USER`: PIA username (`youruser`)
- `VPN_PASS`: PIA password (`yourpass`)
- `HTTP_PROXY_PORT`: `4231` (host port mapped to container 8888)

### File Locations
- `/config/vpnjumper/vpnjumper.py` - Main script
- `/config/vpnjumper/region_index` - Current rotation index (auto-created)
- `/config/gluetun/` - Gluetun volume mount for VPN config persistence

## HTTP Proxy Usage

After rotation, the HTTP proxy is available at `http://localhost:4231` in stealth mode (no proxy headers added to requests):

```bash
# Test the proxy
curl -x http://localhost:4231 https://api.ipify.org

# Use with other tools
export HTTP_PROXY=http://localhost:4231
export HTTPS_PROXY=http://localhost:4231
```

## Region List

97 regions available including:
- US: Atlanta, California, Chicago, Denver, East, Florida, Houston, Las Vegas, New York, Seattle, Silicon Valley, Texas, Washington DC, West
- UK: London, Manchester, Southampton
- DE: Berlin, Frankfurt
- CA: Montreal, Ontario, Toronto, Vancouver
- AU: Melbourne, Perth, Sydney
- And 80+ more countries

Run `--list` to see the full rotation order.

## Architecture

```
┌─────────────────────────────────────────┐
│         VPN Jumper (Python)             │
│  - Reads/writes region_index            │
│  - Stops old gluetun container          │
│  - Starts new gluetun with new region   │
└────────────────┬────────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────────┐
│         Gluetun Container               │
│  - qmcgaw/gluetun:latest                │
│  - OpenVPN connection to PIA            │
│  - Built-in HTTP proxy (port 8888)      │
│  - Stealth mode (no proxy headers)      │
└────────────────┬────────────────────────┘
                 │
                 ▼
        Port 4231 (host)
        HTTP Proxy Endpoint
```

## Logging

Logs are output to stdout with timestamps:
```
2026-05-16 09:21:33 - INFO - ============================================================
2026-05-16 09:21:33 - INFO - VPN Jumper starting rotation
2026-05-16 09:21:33 - INFO - ============================================================
2026-05-16 09:21:33 - INFO - Current index: 42
2026-05-16 09:21:33 - INFO - Next region: Netherlands
...
```

## Troubleshooting

### Docker SDK not available
```
Warning: docker SDK not installed, using subprocess fallback
```
Install with: `pip install docker`

### Container fails to start
Check logs:
```bash
docker logs gluetun
```

### Permission denied for /dev/net/tun
Ensure TUN device exists:
```bash
ls -la /dev/net/tun
# If missing:
sudo mkdir -p /dev/net
sudo mknod /dev/net/tun c 10 200
sudo chmod 666 /dev/net/tun
```

### Reset rotation index
```bash
echo "0" > /config/vpnjumper/region_index
```

## Security Notes

- **Credentials are hardcoded** - modify `VPN_USER` and `VPN_PASS` in the script for your PIA account
- **No proxy authentication** - HTTP proxy accepts any request from localhost (trusted network assumption)
- **Volume persistence** - Gluetun config stored in `/config/gluetun/` for faster reconnects

## License

MIT License - feel free to modify and distribute.
