#!/usr/bin/env python3
"""
VPN Jumper - Rotates VPN regions by rebuilding gluetun container only
Uses gluetun's internal HTTP proxy with stealth mode, eliminating second container

Usage:
    python3 vpnjumper.py              # Rotate once
    python3 vpnjumper.py --watch      # Continuous rotation every 5 minutes
    python3 vpnjumper.py --list       # List all regions
"""

import os
import sys
import time
import argparse
import logging
import tempfile
from pathlib import Path

# Try to import docker, fall back to subprocess if not available
try:
    import docker
    DOCKER_SDK_AVAILABLE = True
except ImportError:
    DOCKER_SDK_AVAILABLE = False
    print("Warning: docker SDK not installed, using subprocess fallback")

# Configuration
CONFIG_DIR = Path("/config/vpnjumper")
INDEX_FILE = CONFIG_DIR / "region_index"
GLUETUN_VOLUME = "/config/gluetun"
HTTP_PROXY_PORT = 4231

# Private Internet Access VPN credentials
VPN_USER = "yours"
VPN_PASS = "secret"

# All available regions
REGIONS = [
    "AU Melbourne", "AU Perth", "AU Sydney", "Albania", "Algeria", "Andorra",
    "Argentina", "Armenia", "Austria", "Bahamas", "Bangladesh", "Belgium",
    "Brazil", "Bulgaria", "CA Montreal", "CA Ontario", "CA Toronto", "CA Vancouver",
    "Cambodia", "China", "Cyprus", "Czech Republic", "DE Berlin", "DE Frankfurt",
    "Denmark", "Egypt", "Estonia", "Finland", "France", "Georgia", "Greece",
    "Greenland", "Hong Kong", "Hungary", "Iceland", "India", "Ireland",
    "Isle Of Man", "Israel", "Italy", "Japan", "Kazakhstan", "Latvia",
    "Liechtenstein", "Lithuania", "Luxembourg", "Macao", "Macedonia", "Malta",
    "Mexico", "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco",
    "Netherlands", "New Zealand", "Nigeria", "Norway", "Panama", "Philippines",
    "Poland", "Portugal", "Qatar", "Romania", "Saudi Arabia", "Serbia",
    "Singapore", "Slovakia", "South Africa", "Spain", "Sri Lanka", "Sweden",
    "Switzerland", "Taiwan", "Turkey", "UK London", "UK Manchester", "UK Southampton",
    "US Atlanta", "US California", "US Chicago", "US Denver", "US East", "US Florida",
    "US Houston", "US Las Vegas", "US New York", "US Seattle", "US Silicon Valley",
    "US Texas", "US Washington Dc", "US West", "Ukraine", "United Arab Emirates",
    "Venezuela", "Vietnam"
]

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_current_index():
    """Read current region index from file."""
    if INDEX_FILE.exists():
        try:
            return int(INDEX_FILE.read_text().strip())
        except (ValueError, IOError) as e:
            logger.warning(f"Failed to read index file: {e}, starting from 0")
    return 0


def save_index(index):
    """Save current region index to file."""
    INDEX_FILE.write_text(str(index))
    logger.debug(f"Saved index: {index}")


def get_next_region(current_index):
    """Get the next region in rotation."""
    return REGIONS[current_index % len(REGIONS)]


def stop_container(client, name):
    """Stop and remove a container if it exists."""
    try:
        container = client.containers.get(name)
        if container.status != 'exited':
            logger.info(f"Stopping container: {name}")
            container.stop(timeout=10)
        logger.info(f"Removing container: {name}")
        container.remove()
    except docker.errors.NotFound:
        logger.debug(f"Container {name} not found, skipping")
    except Exception as e:
        logger.warning(f"Error stopping {name}: {e}")


def start_gluetun(client, region):
    """Start the gluetun VPN container with built-in HTTP proxy."""
    logger.info(f"Starting gluetun with region: {region}")
    
    try:
        # Ensure the gluetun volume directory exists
        Path(GLUETUN_VOLUME).mkdir(parents=True, exist_ok=True)
        
        container = client.containers.run(
            "qmcgaw/gluetun:latest",
            name="gluetun",
            detach=True,
            cap_add=["NET_ADMIN"],
            devices=["/dev/net/tun:/dev/net/tun"],
            volumes={GLUETUN_VOLUME: {"bind": "/gluetun", "mode": "rw"}},
            hostname="gluetun",
            environment={
                "VPN_SERVICE_PROVIDER": "private internet access",
                "VPN_TYPE": "openvpn",
                "OPENVPN_USER": VPN_USER,
                "OPENVPN_PASSWORD": VPN_PASS,
                "SERVER_REGIONS": region,
                # HTTP proxy configuration - stealth mode, no auth
                "HTTPPROXY": "on",
                "HTTPPROXY_LOG": "on",
                "HTTPPROXY_STEALTH": "on",  # Don't add proxy headers to requests
                # No HTTPPROXY_USER/HTTPPROXY_PASS - no authentication needed on trusted network
            },
            ports={8888: HTTP_PROXY_PORT},
            restart_policy={"Name": "no"},
        )
        logger.info(f"Gluetun container started: {container.id[:12]}")
        logger.info(f"HTTP proxy available at http://localhost:{HTTP_PROXY_PORT}")
        return container
    except Exception as e:
        logger.error(f"Failed to start gluetun: {e}")
        raise


def wait_for_container(container, timeout=60):
    """Wait for container to be healthy/running."""
    logger.info(f"Waiting for container {container.name} to be ready...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        container.reload()
        if container.status == 'running':
            # Check logs for any obvious errors
            logs = container.logs(tail=20).decode('utf-8')
            if 'error' in logs.lower() and 'starting' not in logs.lower():
                logger.warning(f"Container logs show potential issues:\n{logs[:500]}")
            logger.info(f"Container {container.name} is running")
            return True
        time.sleep(2)
    
    logger.warning(f"Timeout waiting for {container.name}")
    return False


def rotate_vpn(sdk_mode=True):
    """Main VPN rotation function."""
    logger.info("=" * 60)
    logger.info("VPN Jumper starting rotation")
    logger.info("=" * 60)
    
    # Get current index and calculate next region
    current_index = get_current_index()
    next_index = (current_index + 1) % len(REGIONS)
    region = get_next_region(current_index)
    
    logger.info(f"Current index: {current_index}")
    logger.info(f"Next region: {region}")
    logger.info(f"Total regions: {len(REGIONS)}")
    
    if sdk_mode and DOCKER_SDK_AVAILABLE:
        _rotate_with_sdk(region, current_index, next_index)
    else:
        _rotate_with_subprocess(region, current_index, next_index)


def _rotate_with_sdk(region, current_index, next_index):
    """Rotate using Docker Python SDK."""
    try:
        client = docker.from_env()
    except Exception as e:
        logger.error(f"Failed to connect to Docker daemon: {e}")
        logger.info("Falling back to subprocess mode")
        _rotate_with_subprocess(region, current_index, next_index)
        return
    
    try:
        # Stop and remove existing gluetun container only (no httpproxy anymore)
        stop_container(client, "gluetun")
        
        # Give Docker a moment to clean up
        time.sleep(2)
        
        # Start new gluetun with built-in HTTP proxy
        gluetun = start_gluetun(client, region)
        
        # Wait for gluetun to be ready
        if not wait_for_container(gluetun, timeout=90):
            logger.error("Gluetun failed to start properly")
            # Show recent logs for debugging
            try:
                logs = gluetun.logs(tail=50).decode('utf-8')
                logger.info(f"Gluetun logs:\n{logs}")
            except:
                pass
            return
        
        # Save the next index for subsequent runs
        save_index(next_index)
        
        logger.info("=" * 60)
        logger.info(f"VPN rotation complete!")
        logger.info(f"Region: {region}")
        logger.info(f"HTTP proxy available on port {HTTP_PROXY_PORT} (gluetun built-in, stealth mode)")
        logger.info(f"Next region will be: {get_next_region(next_index)}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Rotation failed: {e}")
        raise


def _rotate_with_subprocess(region, current_index, next_index):
    """Rotate using subprocess calls to docker CLI."""
    import subprocess
    
    try:
        # Stop and remove gluetun container only (no httpproxy anymore)
        logger.info("Stopping existing container...")
        subprocess.run(["docker", "stop", "gluetun"], check=False, capture_output=True)
        subprocess.run(["docker", "rm", "gluetun"], check=False, capture_output=True)
        
        time.sleep(2)
        
        # Ensure volume directory exists
        Path(GLUETUN_VOLUME).mkdir(parents=True, exist_ok=True)
        
        # Start gluetun with built-in HTTP proxy (stealth mode, no auth)
        logger.info(f"Starting gluetun with region: {region}")
        gluetun_cmd = [
            "docker", "run", "-d",
            "--name", "gluetun",
            "--cap-add=NET_ADMIN",
            "--device=/dev/net/tun:/dev/net/tun",
            "-v", f"{GLUETUN_VOLUME}:/gluetun",
            "-e", "VPN_SERVICE_PROVIDER=private internet access",
            "-e", "VPN_TYPE=openvpn",
            "-e", f"OPENVPN_USER={VPN_USER}",
            "-e", f"OPENVPN_PASSWORD={VPN_PASS}",
            "-e", f"SERVER_REGIONS={region}",
            # HTTP proxy configuration - stealth mode, no auth
            "-e", "HTTPPROXY=on",
            "-e", "HTTPPROXY_LOG=on",
            "-e", "HTTPPROXY_STEALTH=on",  # Don't add proxy headers
            # No HTTPPROXY_USER/HTTPPROXY_PASS - no authentication needed
            "-p", f"{HTTP_PROXY_PORT}:8888",
            "--hostname", "gluetun",
            "qmcgaw/gluetun:latest"
        ]
        
        result = subprocess.run(gluetun_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error(f"Gluetun start failed: {result.stderr}")
            return
        logger.info(f"Gluetun started: {result.stdout.strip()[:12]}")
        logger.info(f"HTTP proxy available at http://localhost:{HTTP_PROXY_PORT} (stealth mode)")
        
        # Wait for gluetun
        time.sleep(60)  # Give VPN time to connect
        
        # Save index
        save_index(next_index)
        
        logger.info("=" * 60)
        logger.info(f"VPN rotation complete!")
        logger.info(f"Region: {region}")
        logger.info(f"HTTP proxy available on port {HTTP_PROXY_PORT} (gluetun built-in, stealth mode)")
        logger.info(f"Next region will be: {get_next_region(next_index)}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Rotation failed: {e}")
        raise


def list_regions():
    """Print all available regions."""
    print(f"\nAvailable VPN regions ({len(REGIONS)} total):\n")
    for i, region in enumerate(REGIONS):
        marker = " <-- NEXT" if i == (get_current_index() + 1) % len(REGIONS) else ""
        print(f"  [{i:2d}] {region}{marker}")
    print()


def watch_mode(interval=300):
    """Run in continuous rotation mode."""
    logger.info(f"Starting watch mode (interval: {interval}s)")
    while True:
        try:
            rotate_vpn()
            logger.info(f"Sleeping for {interval} seconds...")
            time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Watch mode stopped by user")
            break
        except Exception as e:
            logger.error(f"Rotation failed: {e}")
            logger.info("Retrying in 60 seconds...")
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="VPN Jumper - Rotate VPN regions")
    parser.add_argument("--list", action="store_true", help="List all regions")
    parser.add_argument("--watch", action="store_true", help="Continuous rotation mode")
    parser.add_argument("--interval", type=int, default=300, help="Rotation interval in seconds (default: 300)")
    parser.add_argument("--cli", action="store_true", help="Force CLI mode instead of SDK")
    
    args = parser.parse_args()
    
    if args.list:
        list_regions()
        return
    
    if args.watch:
        watch_mode(args.interval)
        return
    
    # Single rotation
    sdk_mode = not args.cli
    rotate_vpn(sdk_mode=sdk_mode)


if __name__ == "__main__":
    main()
