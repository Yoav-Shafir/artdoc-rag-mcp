#!/bin/bash
# Lambda start script: the Lambda Web Adapter layer forwards web requests to this server on port 8080.
exec python -m mcp_server.server --http
