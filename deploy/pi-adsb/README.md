# deploy/pi-adsb — optional ADS-B receiver (deferred)

Spare Pi 3/4 + RTL-SDR running dump1090 for local aircraft reception. **Not part of the MVP** — the aircraft tile ships on the OpenSky API first (see `integrations/aircraft`). This directory holds the deployment recipe when/if the local receiver task is scheduled. Isolated workload: its absence must be invisible to everything except the aircraft tile's data source.
