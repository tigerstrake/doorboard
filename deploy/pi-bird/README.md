# deploy/pi-bird — BirdNET-Go node deployment

This document outlines the deployment, installation, and configuration procedure for the dedicated BirdNET-Go node (Raspberry Pi 4) isolating bird detection from the core door controller plane.

## 1. Hardware Requirements & Setup
*   **Host:** Raspberry Pi 4 (2GB RAM or higher recommended).
*   **Microphone:** USB boundary/window microphone or external outdoor microphone connected via USB audio adapter.
*   **Placement Tips:** Mount the microphone near a window or exterior wall, ideally sheltered from direct wind and rain, to capture high-quality ambient bird songs.

## 2. Installation
We pin the BirdNET-Go deployment to a stable version (e.g., `v1.4.0`) to prevent breaking API changes.

### Option A: Docker Compose (Recommended)
Create `/opt/birdnet-go/docker-compose.yml`:

```yaml
version: "3.8"

services:
  birdnet-go:
    image: tphakala/birdnet-go:v1.4.0
    container_name: birdnet-go
    restart: unless-stopped
    network_mode: host
    devices:
      - "/dev/snd:/dev/snd"
    volumes:
      - ./data:/data
    environment:
      - TZ=UTC
```

Start the service:
```bash
docker compose up -d
```

### Option B: Binary Install & Systemd Service
1. Download the release binary matching the `arm64` architecture.
2. Place the binary in `/usr/local/bin/birdnet-go`.
3. Create a systemd service file at `/etc/systemd/system/birdnet-go.service`:

```ini
[Unit]
Description=BirdNET-Go audio detection service
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/birdnet-go --data-dir=/var/lib/birdnet-go
Restart=always
RestartSec=5
User=birdnet

[Install]
WantedBy=multi-user.target
```

## 3. Configuration & Privacy Invariants

Configure the following options through the web interface (port `8080` by default) or via the command line options:

### A. Privacy Invariant: Disable Raw Audio Retention
To comply with ARCHITECTURE.md §9 (privacy invariants), **raw audio retention must be disabled**. The Pi must only retain metadata detections.
*   Set **Audio Retention Days** to `0` or disable it.
*   Command-line flag: Ensure no `--save-audio` or `--audio-retention` flags are enabled.

### B. Geographic & Scientific Filters
*   **Latitude/Longitude:** Set your exact coordinate location so BirdNET-Go uses the correct regional species model.
*   **Confidence Threshold:** Set the default confidence threshold to `0.70` (corresponds to `BIRDNET_CONFIDENCE_THRESHOLD`).
*   **Regional Species Filter:** Restrict detections to species known to occur in your local area by providing your coordinates.
