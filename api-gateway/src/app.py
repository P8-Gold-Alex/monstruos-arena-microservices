# pokemon-arena-microservices/api-gateway/src/app.py

import os
import logging
import requests as http

from flask import Flask, jsonify, request, Response
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [api-gateway] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

# URLs de servicios internos — resueltos por DNS de Docker Compose
POKEDEX_URL = os.getenv("POKEDEX_SERVICE_URL", "http://pokedex-service:5001")
TEAM_URL    = os.getenv("TEAM_SERVICE_URL",    "http://team-service:5002")
BATTLE_URL  = os.getenv("BATTLE_SERVICE_URL",  "http://battle-service:5003")

HTTP_TIMEOUT = 10  # segundos

# Cabeceras que NO se reenvían al servicio destino (hop-by-hop)
HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade", "host",
}

# ---------------------------------------------------------------------------
# Núcleo del proxy — reutilizable para todas las rutas
# ---------------------------------------------------------------------------

def proxy(
    target_url: str,
    method: str | None = None,
    *,
    extra_params: dict | None = None,
) -> Response:
    """
    Reenvía la petición actual al `target_url` usando el mismo método HTTP,
    cabeceras (filtradas) y body que llegaron al gateway.

    Args:
        target_url:    URL completa del servicio interno.
        method:        Permite sobreescribir el método HTTP (útil en tests).
        extra_params:  Query-params adicionales a fusionar con los del cliente.

    Returns:
        Flask Response con el status code, cabeceras y body del servicio interno.
    """
    method = (method or request.method).upper()

    # --- Cabeceras: filtrar hop-by-hop y añadir X-Forwarded-For ---
    fwd_headers = {
        k: v for k, v in request.headers
        if k.lower() not in HOP_BY_HOP_HEADERS
    }
    fwd_headers["X-Forwarded-For"] = request.remote_addr
    fwd_headers["X-Gateway"]       = "pokemon-api-gateway/1.0"

    # --- Query params ---
    params = dict(request.args)
    if extra_params:
        params.update(extra_params)

    # --- Body ---
    body = request.get_data()  # bytes crudos; preserva cualquier Content-Type

    log.info("PROXY %s %s → %s", method, request.path, target_url)

    try:
        upstream = http.request(
            method=method,
            url=target_url,
            headers=fwd_headers,
            params=params or None,
            data=body or None,
            timeout=HTTP_TIMEOUT,
            allow_redirects=False,
        )
    except http.exceptions.ConnectionError as exc:
        log.error("ConnectionError al contactar %s: %s", target_url, exc)
        return _gateway_error(
            502,
            f"No se pudo conectar al servicio interno. "
            f"Verifica que esté levantado ({target_url}).",
        )
    except http.exceptions.Timeout:
        log.error("Timeout al contactar %s (límite: %ds)", target_url, HTTP_TIMEOUT)
        return _gateway_error(
            504,
            f"El servicio interno tardó más de {HTTP_TIMEOUT}s en responder.",
        )
    except http.exceptions.RequestException as exc:
        log.error("RequestException inesperada: %s", exc)
        return _gateway_error(500, f"Error inesperado en el gateway: {exc}")

    # --- Filtrar cabeceras de respuesta hop-by-hop ---
    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS
    }

    log.info(
        "RESP %s %s ← HTTP %d (%d bytes)",
        method, request.path, upstream.status_code, len(upstream.content),
    )

    return Response(
        response=upstream.content,
        status=upstream.status_code,
        headers=resp_headers,
    )


def _gateway_error(status: int, message: str) -> Response:
    """Respuesta de error estándar generada por el propio gateway."""
    body = {"success": False, "error": message, "source": "api-gateway"}
    return Response(
        response=__import__("json").dumps(body),
        status=status,
        mimetype="application/json",
    )


# ---------------------------------------------------------------------------
# Rutas — Pokédex
# ---------------------------------------------------------------------------

@app.route("/api/pokedex", methods=["GET"])
def api_list_pokedex():
    """GET /api/pokedex[?type=Fire&limit=20&offset=0]"""
    return proxy(f"{POKEDEX_URL}/pokedex")


@app.route("/api/pokedex/<int:species_id>", methods=["GET"])
def api_get_species(species_id: int):
    """GET /api/pokedex/<id>"""
    return proxy(f"{POKEDEX_URL}/pokedex/{species_id}")


@app.route("/api/pokedex/name/<string:name>", methods=["GET"])
def api_get_species_by_name(name: str):
    """GET /api/pokedex/name/<name>"""
    return proxy(f"{POKEDEX_URL}/pokedex/name/{name}")


@app.route("/api/moves", methods=["GET"])
def api_list_moves():
    """GET /api/moves[?type=Fire&category=Special]"""
    return proxy(f"{POKEDEX_URL}/moves")


@app.route("/api/moves/<int:move_id>", methods=["GET"])
def api_get_move(move_id: int):
    """GET /api/moves/<id>"""
    return proxy(f"{POKEDEX_URL}/moves/{move_id}")


# ---------------------------------------------------------------------------
# Rutas — Team
# ---------------------------------------------------------------------------

@app.route("/api/players", methods=["POST"])
def api_create_player():
    """POST /api/players  — Crear jugador."""
    return proxy(f"{TEAM_URL}/players")


@app.route("/api/players/<int:player_id>", methods=["GET"])
def api_get_player(player_id: int):
    """GET /api/players/<player_id>"""
    return proxy(f"{TEAM_URL}/players/{player_id}")


@app.route("/api/team/<int:player_id>", methods=["GET"])
def api_get_team(player_id: int):
    """GET /api/team/<player_id>  — Equipo completo con stats calculados."""
    return proxy(f"{TEAM_URL}/team/{player_id}")


@app.route("/api/team/<int:player_id>/add", methods=["POST"])
def api_add_pokemon(player_id: int):
    """POST /api/team/<player_id>/add  — Añadir Pokémon al equipo."""
    return proxy(f"{TEAM_URL}/team/{player_id}/add")


@app.route("/api/team/<int:player_id>/remove/<int:slot>", methods=["DELETE"])
def api_remove_pokemon(player_id: int, slot: int):
    """DELETE /api/team/<player_id>/remove/<slot>"""
    return proxy(f"{TEAM_URL}/team/{player_id}/remove/{slot}")


# ---------------------------------------------------------------------------
# Rutas — Battle (stub listo para Fase 3)
# ---------------------------------------------------------------------------

@app.route("/api/battle/create", methods=["POST"])
def api_battle_create():
    """POST /api/battle/create"""
    return proxy(f"{BATTLE_URL}/battle/create")


@app.route("/api/battle/<string:battle_id>/turn", methods=["POST"])
def api_battle_turn(battle_id: str):
    """POST /api/battle/<battle_id>/turn"""
    return proxy(f"{BATTLE_URL}/battle/{battle_id}/turn")


@app.route("/api/battle/<string:battle_id>", methods=["GET"])
def api_get_battle(battle_id: str):
    """GET /api/battle/<battle_id>"""
    return proxy(f"{BATTLE_URL}/battle/{battle_id}")


# ---------------------------------------------------------------------------
# Health-check agregado — consulta todos los servicios internos
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def api_health():
    """
    Consulta el /health de cada servicio y devuelve un resumen.
    Útil para dashboards y Docker healthchecks del gateway.
    """
    services = {
        "pokedex-service": f"{POKEDEX_URL}/health",
        "team-service":    f"{TEAM_URL}/health",
        "battle-service":  f"{BATTLE_URL}/health",
    }

    results  = {}
    all_ok   = True

    for name, url in services.items():
        try:
            r = http.get(url, timeout=3)
            results[name] = {"status": "ok" if r.ok else "degraded", "http": r.status_code}
            if not r.ok:
                all_ok = False
        except http.exceptions.RequestException as exc:
            results[name] = {"status": "unreachable", "error": str(exc)}
            all_ok = False

    return jsonify({
        "success": all_ok,
        "gateway": "ok",
        "services": results,
    }), 200 if all_ok else 207   # 207 Multi-Status si alguno falla


@app.route("/health", methods=["GET"])
def gateway_self_health():
    """Health-check del propio gateway (sin consultar servicios internos)."""
    return jsonify({"success": True, "service": "api-gateway"})


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    log.info("Iniciando api-gateway en el puerto 5000…")
    app.run(host="0.0.0.0", port=5000, debug=False)