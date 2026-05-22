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
import subprocess
import random
import json
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
CONFIG_FILE = CONFIG_DIR / "config.txt"
GLUETUN_VOLUME = "/config/vpnjumper/gluetun"
HTTP_PROXY_PORT = 4231
SERVERS_JSON_PATH = Path("/config/vpnjumper/gluetun/servers.json")

# Private Internet Access VPN credentials - loaded from config.txt
VPN_USER = None
VPN_PASS = None


def load_vpn_credentials():
    """Load VPN credentials from config.txt file."""
    global VPN_USER, VPN_PASS
    
    if not CONFIG_FILE.exists():
        logger.error(f"Config file not found at {CONFIG_FILE}")
        logger.error("Please create config.txt with VPN_USER and VPN_PASS variables")
        sys.exit(1)
    
    try:
        # Execute config file to set global variables
        config_globals = {}
        with open(CONFIG_FILE, 'r') as f:
            exec(f.read(), config_globals)
        
        VPN_USER = config_globals.get('VPN_USER')
        VPN_PASS = config_globals.get('VPN_PASS')
        
        if not VPN_USER or not VPN_PASS:
            logger.error("VPN_USER and VPN_PASS must be defined in config.txt")
            sys.exit(1)
        
        logger.info("VPN credentials loaded from config.txt")
    except Exception as e:
        logger.error(f"Failed to load credentials from config.txt: {e}")
        sys.exit(1)


# Setup logging BEFORE loading credentials
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default region if servers.json doesn't exist
DEFAULT_REGION = "CA Montreal"

# Load credentials at module initialization
load_vpn_credentials()


def load_regions_from_servers_json():
    """Load all regions from gluetun's servers.json for Private Internet Access."""
    if not SERVERS_JSON_PATH.exists():
        logger.info(f"servers.json not found at {SERVERS_JSON_PATH}")
        return None
    
    try:
        with open(SERVERS_JSON_PATH, 'r') as f:
            data = json.load(f)
        
        if 'private internet access' not in data:
            logger.warning("'private internet access' not found in servers.json")
            return None
        
        pia_data = data['private internet access']
        if 'servers' not in pia_data:
            logger.warning("'servers' not found in 'private internet access' section")
            return None
        
        # Extract unique regions
        regions = set()
        for server in pia_data['servers']:
            if 'region' in server:
                regions.add(server['region'])
        
        if not regions:
            logger.warning("No regions found in servers.json")
            return None
        
        logger.info(f"Loaded {len(regions)} unique regions from servers.json")
        return sorted(list(regions))
    
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse servers.json: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading servers.json: {e}")
        return None


def get_randomized_regions():
    """Get regions from servers.json, randomized. Fall back to default if not found."""
    regions = load_regions_from_servers_json()
    
    if regions is None or len(regions) == 0:
        logger.info(f"servers.json not available or empty, using default region: {DEFAULT_REGION}")
        return [DEFAULT_REGION]
    
    # Randomize the order
    random.shuffle(regions)
    logger.info(f"Regions randomized, total: {len(regions)}")
    
    return regions


def get_random_region():
    """Get a random region from the shuffled regions list."""
    regions = get_randomized_regions()
    return random.choice(regions)


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


def wait_for_container(container, timeout=120):
    """Wait for container to be running AND VPN connected."""
    logger.info(f"Waiting for container {container.name} to be ready...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        container.reload()
        if container.status == 'running':
            # Check logs for VPN connection success
            logs = container.logs(tail=50).decode('utf-8')
            
            # Look for successful VPN connection
            if 'vpn successfully connected' in logs.lower() or 'public ip' in logs.lower():
                logger.info(f"Container {container.name} is running with VPN connected")
                return True
            
            # Check for obvious errors (but ignore startup messages)
            if 'error' in logs.lower() and 'starting' not in logs.lower():
                logger.warning(f"Container logs show potential issues:\n{logs[:500]}")
            else:
                logger.debug(f"Container running, waiting for VPN... (still connecting)")
        time.sleep(3)
    
    # Timeout - show recent logs
    logs = container.logs(tail=30).decode('utf-8')
    logger.warning(f"Timeout waiting for {container.name} to connect VPN")
    logger.info(f"Recent logs:\n{logs[:800]}")
    return False


def rotate_vpn(sdk_mode=True):
    """Main VPN rotation function."""
    logger.info("=" * 60)
    logger.info("VPN Jumper starting rotation")
    logger.info("=" * 60)
    
    # Get a random region
    region = get_random_region()
    
    logger.info(f"Selected region: {region}")
    
    if sdk_mode and DOCKER_SDK_AVAILABLE:
        _rotate_with_sdk(region)
    else:
        _rotate_with_subprocess(region)


def _rotate_with_sdk(region):
    """Rotate using Docker Python SDK."""
    try:
        client = docker.from_env()
    except Exception as e:
        logger.error(f"Failed to connect to Docker daemon: {e}")
        logger.info("Falling back to subprocess mode")
        _rotate_with_subprocess(region)
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
        
        logger.info("=" * 60)
        logger.info(f"VPN rotation complete!")
        logger.info(f"Region: {region} {get_proxy_ip()}")
        logger.info(f"HTTP proxy available on port {HTTP_PROXY_PORT} (gluetun built-in, stealth mode)")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Rotation failed: {e}")
        raise


def _rotate_with_subprocess(region):
    """Rotate using subprocess calls to docker CLI."""
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
        
        logger.info("=" * 60)
        logger.info(f"VPN rotation complete!")
        logger.info(f"Region: {region} {get_proxy_ip()}")
        logger.info(f"HTTP proxy available on port {HTTP_PROXY_PORT} (gluetun built-in, stealth mode)")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Rotation failed: {e}")
        raise

def get_proxy_ip(max_retries=3, retry_delay=5, auto_rotate=True):
    import requests
    proxies = {
        "http": "http://localhost:4231",
        "https": "http://localhost:4231"
    }
    
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug(f"Getting proxy IP (attempt {attempt}/{max_retries})...")
            response = requests.get(
                "https://ifconfig.me",
                proxies=proxies,
                timeout=10
            )
            # Check if the server actually gave us something
            response.raise_for_status()
            ip = response.text.strip()
            logger.info(f"Successfully retrieved IP: {ip}")
            return ip
        except requests.exceptions.ProxyError as e:
            logger.warning(f"Proxy error (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                logger.error(f"Proxy error after {max_retries} attempts - VPN proxy may not be ready")
                if auto_rotate:
                    logger.warning("Bad endpoint detected, triggering automatic rotation...")
                    # Immediately rotate to next region
                    rotate_vpn(sdk_mode=True)
                    return None
                return None
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                logger.error(f"Connection error after {max_retries} attempts")
                return None
        except requests.exceptions.RequestException as e:
            # If this  fails, it's just because the world is ending
            logger.error(f"Request failed (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
            else:
                return None
    
    return None

def list_regions():
    """Print all available regions."""
    regions = get_randomized_regions()
    print(f"\nAvailable VPN regions ({len(regions)} total):\n")
    for i, region in enumerate(regions):
        print(f"  [{i:2d}] {region}")
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
