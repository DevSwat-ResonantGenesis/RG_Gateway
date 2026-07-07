# RG Gateway

> **Part of the [ResonantGenesis](https://resonant.dev-swat.com) platform** — Central API gateway routing all external requests to backend services.

[![Status: Production](https://img.shields.io/badge/Status-Production-brightgreen.svg)]()
[![Port: 8001](https://img.shields.io/badge/Port-8001-orange.svg)]()
[![License: RG Source Available](https://img.shields.io/badge/License-RG%20Source%20Available-blue.svg)](LICENSE.txt)

## Features
- JWT auth middleware, rate limiting, CORS
- Reverse proxy to 30+ backend services
- SSE streaming proxy for chat and IDE
- WebSocket upgrade support

## Quick Start
```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Deployment
- **Container**: `gateway` | **Port**: 8001 → 8000
- **Server path**: `/home/deploy/RG_Gateway`

---
**Organization**: [DevSwat-ResonantGenesis](https://github.com/DevSwat-ResonantGenesis) | **Platform**: [resonant.dev-swat.com](https://resonant.dev-swat.com)
