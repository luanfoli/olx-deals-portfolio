"""
MCP server do olx-deals — gerado de openapi.yaml (fonte única de verdade).

FastMCP consome a spec OpenAPI e expõe cada operação como tool, chamando a
API REST por HTTP (zero duplicação de lógica). Registro no opencode:
ver ~/.config/opencode/opencode.jsonc.

- PUT /api/config é excluído (substitui a lista inteira de products —
  destrutivo); agentes usam o CRUD de products.
- Timeout alto (120s): triggerScrape pode levar minutos; prefira disparar e
  fazer polling de getScrapeStatus até running=false.
- Auth opt-in: se OLX_API_TOKEN estiver setado no ambiente deste processo,
  ele é anexado como header X-API-Token em todas as chamadas.
"""

import os

import httpx
import yaml
from fastmcp import FastMCP
from fastmcp.server.providers.openapi import MCPType, RouteMap

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_URL = os.environ.get("OLX_DEALS_URL", "http://127.0.0.1:8765")
_token = os.environ.get("OLX_API_TOKEN")

# Fase 6: openapi.yaml é fonte única gerada de src/olx_deals/interfaces/api/schemas.py (Pydantic)
# Fallback: se arquivo não existir (ex.: install via src), gera a partir de schemas
try:
    with open(os.path.join(BASE_DIR, "openapi.yaml"), encoding="utf-8") as _f:
        _spec = yaml.safe_load(_f)
except FileNotFoundError:
    try:
        from olx_deals.interfaces.api.schemas import generate_openapi_from_schemas
        _spec = generate_openapi_from_schemas()
    except Exception as e:
        raise RuntimeError(f"openapi.yaml não encontrado e geração via schemas falhou: {e}") from e

_client = httpx.AsyncClient(
    base_url=BASE_URL,
    headers={"X-API-Token": _token} if _token else {},
    timeout=120,
)

mcp = FastMCP.from_openapi(
    openapi_spec=_spec,
    client=_client,
    name="olx-deals",
    route_maps=[
        RouteMap(
            methods=["PUT"],
            pattern=r".*/api/config$",
            mcp_type=MCPType.EXCLUDE,
        ),
    ],
)

if __name__ == "__main__":
    mcp.run()
