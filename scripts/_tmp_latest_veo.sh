#!/usr/bin/env bash
tail -100 /opt/lobster-server/logs/app.log 2>/dev/null | grep -iE "veo|comfly.*路由|invoke_capability|capability_id" | tail -15
