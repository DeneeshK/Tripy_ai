#!/usr/bin/env bash
# One-time setup: downloads a Kerala road-network extract and preprocesses it
# into the routing files OSRM actually serves from. Run this once before
# `docker compose up` the first time (or again if you want to refresh the map
# data). Safe to re-run -- skips the download if the extract is already there.
#
# Why this exists: the app self-hosts OSRM instead of depending on the public
# router.project-osrm.org demo server (no uptime guarantee, rate-limited).
# Self-hosting means WE supply the road data and run the one-time
# extract/partition/customize pipeline that turns raw OpenStreetMap data into
# OSRM's routing files -- this script is that pipeline.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
PBF_NAME="kerala-latest.osm.pbf"
PBF_URL="https://download.openstreetmap.fr/extracts/asia/india/${PBF_NAME}"
OSRM_IMAGE="ghcr.io/project-osrm/osrm-backend:latest"

mkdir -p "$DATA_DIR"

if [ ! -f "$DATA_DIR/$PBF_NAME" ]; then
  echo "Downloading Kerala OSM extract (~170MB) from $PBF_URL ..."
  curl -fL --progress-bar -o "$DATA_DIR/$PBF_NAME.tmp" "$PBF_URL"
  mv "$DATA_DIR/$PBF_NAME.tmp" "$DATA_DIR/$PBF_NAME"
else
  echo "Found existing $PBF_NAME, skipping download."
fi

BASE_NAME="${PBF_NAME%.osm.pbf}"

if [ -f "$DATA_DIR/$BASE_NAME.osrm.mldgr" ]; then
  echo "Routing files already built ($BASE_NAME.osrm.mldgr exists). Delete docker/osrm/data/*.osrm* to force a rebuild."
  exit 0
fi

echo "Extracting road network (osrm-extract, car profile) ..."
docker run --rm -v "$DATA_DIR:/data" "$OSRM_IMAGE" \
  osrm-extract -p /opt/car.lua "/data/$PBF_NAME"

echo "Partitioning (osrm-partition, MLD algorithm) ..."
docker run --rm -v "$DATA_DIR:/data" "$OSRM_IMAGE" \
  osrm-partition "/data/$BASE_NAME.osrm"

echo "Customizing (osrm-customize) ..."
docker run --rm -v "$DATA_DIR:/data" "$OSRM_IMAGE" \
  osrm-customize "/data/$BASE_NAME.osrm"

echo
echo "Done. Routing files are in docker/osrm/data/ -- 'docker compose up' will now serve real Kerala road routing."
