#!/bin/sh
set -e
docker compose up -d
python3 smoke.py
