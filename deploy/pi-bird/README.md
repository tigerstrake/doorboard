# deploy/pi-bird — BirdNET-Go node deployment

This document outlines the deployment, installation, and configuration procedure for the dedicated BirdNET-Go node (Raspberry Pi 4) isolating bird detection from the core door controller plane.

## 1. Hardware Requirements & Setup
*   **Host:** Raspberry Pi 4 (2GB RAM or higher recommended), Raspberry Pi OS Lite 64-bit (Trixie).
*   **Microphone:** USB boundary/window microphone or external outdoor microphone connected via USB audio adapter. Confirm the Pi sees it with `arecord -l` before starting BirdNET-Go.
*   **Placement Tips:** Mount the microphone near a window or exterior wall, ideally sheltered from direct wind and rain, to capture high-quality ambient bird songs.
*   **Network:** Join the Pi to the tailnet (`tailscale up --ssh`) so the NUC can reach it by MagicDNS name.

## 2. Installation
BirdNET-Go uses date-based releases (e.g. `20261014`); there are no semver tags. Pin a specific release to prevent breaking API changes. A native binary + systemd install keeps a 2GB Pi 4 free of Docker overhead.

1. Download the `linux-arm64` release tarball and verify its checksum:

```bash
V=20261014
B=https://github.com/tphakala/birdnet-go/releases/download/$V
curl -fsSLO $B/birdnet-go-linux-arm64-$V.tar.gz
curl -fsSLO $B/checksums.txt
grep "birdnet-go-linux-arm64-$V.tar.gz" checksums.txt | sha256sum -c -
tar xzf birdnet-go-linux-arm64-$V.tar.gz
```

2. Install the bundled shared libraries and the binary:

```bash
sudo cp libonnxruntime.so libtensorflowlite_c.so /usr/local/lib/
sudo ldconfig
sudo install -m 755 birdnet-go /usr/local/bin/birdnet-go
```

3. Create a dedicated service user with microphone access:

```bash
sudo useradd --system --home-dir /var/lib/birdnet-go --shell /usr/sbin/nologin --groups audio birdnet
sudo install -d -o birdnet -g birdnet /var/lib/birdnet-go
```

4. Create `/etc/systemd/system/birdnet-go.service`. The `serve` subcommand is required; without it the binary prints help and exits.

```ini
[Unit]
Description=BirdNET-Go audio detection service
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=birdnet
Group=birdnet
SupplementaryGroups=audio
WorkingDirectory=/var/lib/birdnet-go
ExecStart=/usr/local/bin/birdnet-go serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

5. Start it. On first launch it writes `/var/lib/birdnet-go/.config/birdnet-go/config.yaml`, loads the BirdNET v2.4 model, and serves the web UI on port `8080`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now birdnet-go
```

A Docker image is also published (`ghcr.io/tphakala/birdnet-go:<release>`); see the upstream docs for its volume layout.

## 3. Configuration & Privacy Invariants

Configure the following options in `config.yaml` (restart the service after editing) or through the web interface (port `8080`).

### A. Privacy Invariant: Disable Raw Audio Retention
To comply with ARCHITECTURE.md §9 (privacy invariants), **raw audio retention must be disabled**. The Pi must only retain metadata detections. BirdNET-Go saves a clip for every detection by default, so this must be turned off explicitly:

```yaml
realtime:
    audio:
        export:
            enabled: false
```

### B. Geographic & Scientific Filters
*   **Latitude/Longitude:** Set your coordinates so BirdNET-Go uses the correct regional species model. Use the same coarse `LAT`/`LON` as the control plane `.env`:

```yaml
birdnet:
    latitude: 37.4242
    longitude: -122.1708
    locationconfigured: true
```

*   **Confidence Threshold:** Keep `birdnet.threshold` at `0.70` (the default; corresponds to `BIRDNET_CONFIDENCE_THRESHOLD`).
*   **Regional Species Filter:** The range filter uses the coordinates above to restrict detections to species known to occur in your area.

## 4. Connecting the Control Plane
On the NUC, point the wallboard-worker at the bird Pi in `.env` and enable the feature flag:

```bash
FEATURE_BIRDNET=true
BIRDNET_GO_URL=http://<bird-pi-tailscale-name>:8080
```

Verify from the NUC: `curl "http://<bird-pi-tailscale-name>:8080/api/v2/detections?start_date=$(date +%F)"` should return `{"data": [...], ...}`.
