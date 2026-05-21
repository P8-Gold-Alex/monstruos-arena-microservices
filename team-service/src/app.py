# pokemon-arena-microservices/team-service/src/app.py

import sqlite3
import os
import json
import math
import logging
import requests as http

from flask import Flask, jsonify, request, g
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [team-service] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

DB_DIR  = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "teams.db")

POKEDEX_URL   = os.getenv("POKEDEX_SERVICE_URL", "http://pokedex-service:5001")
MAX_TEAM_SIZE = 6
HTTP_TIMEOUT  = 5  # segundos para llamadas inter-servicio

# ---------------------------------------------------------------------------
# Naturalezas — modificador de stat (Gen 3+, vigente en Gen 9)
# Cada naturaleza aumenta un stat en ×1.1 y baja otro en ×0.9.
# Las neutras no aparecen aquí; si no está en el dict, modificador = 1.0
# ---------------------------------------------------------------------------
NATURE_MODIFIERS: dict[str, dict[str, float]] = {
    "Hardy":   {},
    "Lonely":  {"attack": 1.1, "defense": 0.9},
    "Brave":   {"attack": 1.1, "speed": 0.9},
    "Adamant": {"attack": 1.1, "sp_attack": 0.9},
    "Naughty": {"attack": 1.1, "sp_defense": 0.9},
    "Bold":    {"defense": 1.1, "attack": 0.9},
    "Docile":  {},
    "Relaxed": {"defense": 1.1, "speed": 0.9},
    "Impish":  {"defense": 1.1, "sp_attack": 0.9},
    "Lax":     {"defense": 1.1, "sp_defense": 0.9},
    "Timid":   {"speed": 1.1, "attack": 0.9},
    "Hasty":   {"speed": 1.1, "defense": 0.9},
    "Serious": {},
    "Jolly":   {"speed": 1.1, "sp_attack": 0.9},
    "Naive":   {"speed": 1.1, "sp_defense": 0.9},
    "Modest":  {"sp_attack": 1.1, "attack": 0.9},
    "Mild":    {"sp_attack": 1.1, "defense": 0.9},
    "Quiet":   {"sp_attack": 1.1, "speed": 0.9},
    "Bashful": {},
    "Rash":    {"sp_attack": 1.1, "sp_defense": 0.9},
    "Calm":    {"sp_defense": 1.1, "attack": 0.9},
    "Gentle":  {"sp_defense": 1.1, "defense": 0.9},
    "Sassy":   {"sp_defense": 1.1, "speed": 0.9},
    "Careful": {"sp_defense": 1.1, "sp_attack": 0.9},
    "Quirky":  {},
}

VALID_NATURES = set(NATURE_MODIFIERS.keys())
BATTLE_STATS  = ["attack", "defense", "sp_attack", "sp_defense", "speed"]
ALL_STATS     = ["hp"] + BATTLE_STATS

# ---------------------------------------------------------------------------
# Fórmulas oficiales Gen 9 (Scarlet/Violet)
# ---------------------------------------------------------------------------

def calc_hp(base: int, iv: int, ev: int, level: int) -> int:
    """
    HP = floor(( (2 × Base + IV + floor(EV/4)) × Level ) / 100) + Level + 10
    Caso especial Shedinja: siempre 1 HP.
    """
    return math.floor(((2 * base + iv + math.floor(ev / 4)) * level) / 100) + level + 10


def calc_stat(base: int, iv: int, ev: int, level: int, nature_mod: float) -> int:
    """
    Stat = floor(( floor(( (2 × Base + IV + floor(EV/4)) × Level ) / 100) + 5 ) × Nature)
    """
    inner = math.floor(((2 * base + iv + math.floor(ev / 4)) * level) / 100) + 5
    return math.floor(inner * nature_mod)


def compute_all_stats(
    base_stats: dict,
    ivs: dict,
    evs: dict,
    level: int,
    nature: str,
) -> dict:
    """
    Devuelve un dict con los 6 stats calculados según las fórmulas de Gen 9.
    """
    mods = NATURE_MODIFIERS.get(nature, {})
    computed: dict[str, int] = {}

    computed["hp"] = calc_hp(
        base=base_stats["hp"],
        iv=ivs.get("hp", 31),
        ev=evs.get("hp", 0),
        level=level,
    )

    for stat in BATTLE_STATS:
        computed[stat] = calc_stat(
            base=base_stats[stat],
            iv=ivs.get(stat, 31),
            ev=evs.get(stat, 0),
            level=level,
            nature_mod=mods.get(stat, 1.0),
        )

    return computed


# ---------------------------------------------------------------------------
# Validadores de entrada
# ---------------------------------------------------------------------------

def validate_ivs(ivs: dict) -> list[str]:
    errors = []
    for stat in ALL_STATS:
        val = ivs.get(stat, 31)
        if not isinstance(val, int) or not (0 <= val <= 31):
            errors.append(f"IV '{stat}' debe ser entero entre 0 y 31 (recibido: {val}).")
    return errors


def validate_evs(evs: dict) -> list[str]:
    errors = []
    total  = 0
    for stat in ALL_STATS:
        val = evs.get(stat, 0)
        if not isinstance(val, int) or not (0 <= val <= 252):
            errors.append(f"EV '{stat}' debe ser entero entre 0 y 252 (recibido: {val}).")
        total += val
    if total > 510:
        errors.append(f"La suma total de EVs no puede superar 510 (suma actual: {total}).")
    return errors


def validate_moves(moves: list) -> list[str]:
    """
    moves debe ser un array de exactamente 4 elementos.
    Cada elemento es un move_id (int) o null (slot vacío).
    """
    errors = []
    if not isinstance(moves, list) or len(moves) != 4:
        errors.append("'moves' debe ser un array de exactamente 4 elementos (usa null para slots vacíos).")
        return errors
    for i, m in enumerate(moves):
        if m is not None and not isinstance(m, int):
            errors.append(f"moves[{i}] debe ser un entero (move_id) o null.")
    if all(m is None for m in moves):
        errors.append("El Pokémon debe tener al menos 1 movimiento.")
    return errors


# ---------------------------------------------------------------------------
# Comunicación inter-servicio con el pokedex-service
# ---------------------------------------------------------------------------

def fetch_base_stats(pokedex_id: int) -> tuple[dict | None, str | None]:
    """
    Consulta el pokedex-service para obtener los stats base de una especie.
    Devuelve (base_stats_dict, None) en éxito o (None, mensaje_error) en fallo.
    """
    url = f"{POKEDEX_URL}/pokedex/{pokedex_id}"
    try:
        resp = http.get(url, timeout=HTTP_TIMEOUT)
    except http.exceptions.ConnectionError:
        return None, (
            f"No se pudo conectar al pokedex-service ({POKEDEX_URL}). "
            "Verifica que el servicio esté activo y en la misma red."
        )
    except http.exceptions.Timeout:
        return None, (
            f"El pokedex-service tardó más de {HTTP_TIMEOUT}s en responder. Intenta de nuevo."
        )
    except http.exceptions.RequestException as exc:
        return None, f"Error inesperado al consultar el pokedex-service: {exc}"

    if resp.status_code == 404:
        return None, f"pokedex_id={pokedex_id} no existe en el Pokédex."
    if resp.status_code != 200:
        return None, (
            f"El pokedex-service devolvió un error inesperado "
            f"(HTTP {resp.status_code}): {resp.text[:200]}"
        )

    try:
        payload = resp.json()
        base_stats = payload["data"]["base_stats"]
        species_name = payload["data"]["name"]
    except (KeyError, ValueError) as exc:
        return None, f"Respuesta del pokedex-service tiene formato inesperado: {exc}"

    return {"base_stats": base_stats, "name": species_name}, None


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cur = conn.cursor()

    # ------------------------------------------------------------------
    # Tabla: players
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # Tabla: pokemon_instances
    #
    # Columnas future-proof:
    #   - moves_json        : [move_id|null, move_id|null, move_id|null, move_id|null]
    #   - ivs_json          : {"hp":31,"attack":31,...}
    #   - evs_json          : {"hp":0,"attack":252,...}
    #   - computed_stats_json: {"hp":361,"attack":278,...}  ← calculado en add
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pokemon_instances (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id           INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
            pokedex_id          INTEGER NOT NULL,
            species_name        TEXT    NOT NULL,
            nickname            TEXT,
            level               INTEGER NOT NULL DEFAULT 50,
            nature              TEXT    NOT NULL DEFAULT 'Hardy',
            ability             TEXT,
            moves_json          TEXT    NOT NULL DEFAULT '[null,null,null,null]',
            ivs_json            TEXT    NOT NULL DEFAULT '{}',
            evs_json            TEXT    NOT NULL DEFAULT '{}',
            computed_stats_json TEXT    NOT NULL DEFAULT '{}',
            slot                INTEGER NOT NULL,  -- posición en el equipo (1-6)
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            UNIQUE(player_id, slot)
        )
    """)

    # Seed: dos jugadores de prueba
    cur.execute("""
        INSERT OR IGNORE INTO players (name) VALUES ('Ash'), ('Misty')
    """)

    conn.commit()
    conn.close()
    log.info("teams.db inicializada correctamente en %s", DB_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def instance_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["moves"]          = json.loads(d.pop("moves_json"))
    d["ivs"]            = json.loads(d.pop("ivs_json"))
    d["evs"]            = json.loads(d.pop("evs_json"))
    d["computed_stats"] = json.loads(d.pop("computed_stats_json"))
    return d


def error_response(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status


# ---------------------------------------------------------------------------
# Rutas — Players
# ---------------------------------------------------------------------------

@app.route("/players", methods=["POST"])
def create_player():
    """POST /players  — Crea un nuevo jugador."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()

    if not name:
        return error_response("El campo 'name' es obligatorio.", 400)
    if len(name) > 64:
        return error_response("'name' no puede superar 64 caracteres.", 400)

    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO players (name) VALUES (?) RETURNING id, name, created_at",
            (name,),
        )
        player = dict(cur.fetchone())
        db.commit()
    except sqlite3.IntegrityError:
        return error_response(f"Ya existe un jugador con el nombre '{name}'.", 409)

    return jsonify({"success": True, "data": player}), 201


@app.route("/players/<int:player_id>", methods=["GET"])
def get_player(player_id: int):
    db  = get_db()
    row = db.execute("SELECT * FROM players WHERE id = ?", (player_id,)).fetchone()
    if row is None:
        return error_response(f"Jugador id={player_id} no encontrado.", 404)
    return jsonify({"success": True, "data": dict(row)})


# ---------------------------------------------------------------------------
# Rutas — Team
# ---------------------------------------------------------------------------

@app.route("/team/<int:player_id>", methods=["GET"])
def get_team(player_id: int):
    """
    GET /team/<player_id>
    Devuelve el equipo completo (hasta 6 Pokémon) con stats ya calculados.
    """
    db = get_db()
    if db.execute("SELECT 1 FROM players WHERE id=?", (player_id,)).fetchone() is None:
        return error_response(f"Jugador id={player_id} no encontrado.", 404)

    rows = db.execute("""
        SELECT * FROM pokemon_instances
        WHERE player_id = ?
        ORDER BY slot
    """, (player_id,)).fetchall()

    # Slots vacíos representados como null (future-proof para el battle-service)
    slots: list[dict | None] = [None] * MAX_TEAM_SIZE
    for row in rows:
        idx = row["slot"] - 1  # slot es 1-based
        slots[idx] = instance_row_to_dict(row)

    return jsonify({
        "success": True,
        "meta": {
            "player_id": player_id,
            "total_pokemon": len(rows),
            "max_team_size": MAX_TEAM_SIZE,
        },
        "data": slots,   # array de 6: Pokémon o null
    })


@app.route("/team/<int:player_id>/add", methods=["POST"])
def add_pokemon_to_team(player_id: int):
    """
    POST /team/<player_id>/add

    Body JSON esperado:
    {
        "pokedex_id": 6,
        "nickname":   "Charizard-Kun",   // opcional
        "level":      50,                // 1-100, default 50
        "nature":     "Timid",
        "ability":    "Solar Power",     // opcional
        "moves":      [53, 6, null, null], // array de 4 (move_id o null)
        "ivs": { "hp":31, "attack":31, "defense":31,
                 "sp_attack":31, "sp_defense":31, "speed":31 },
        "evs": { "hp":4,  "attack":0,  "defense":0,
                 "sp_attack":252, "sp_defense":0, "speed":252 }
    }
    """
    db = get_db()

    # 1. Verificar que el jugador existe
    if db.execute("SELECT 1 FROM players WHERE id=?", (player_id,)).fetchone() is None:
        return error_response(f"Jugador id={player_id} no encontrado.", 404)

    # 2. Verificar que el equipo no está lleno
    count = db.execute(
        "SELECT COUNT(*) FROM pokemon_instances WHERE player_id=?", (player_id,)
    ).fetchone()[0]
    if count >= MAX_TEAM_SIZE:
        return error_response(
            f"El equipo ya está completo ({MAX_TEAM_SIZE}/{MAX_TEAM_SIZE} Pokémon).", 409
        )

    # 3. Parsear y validar body
    body = request.get_json(silent=True)
    if not body:
        return error_response("Se esperaba un body JSON válido.", 400)

    pokedex_id = body.get("pokedex_id")
    if not isinstance(pokedex_id, int) or pokedex_id < 1:
        return error_response("'pokedex_id' es obligatorio y debe ser un entero positivo.", 400)

    level = body.get("level", 50)
    if not isinstance(level, int) or not (1 <= level <= 100):
        return error_response("'level' debe ser un entero entre 1 y 100.", 400)

    nature = body.get("nature", "Hardy")
    if nature not in VALID_NATURES:
        return error_response(
            f"'nature' inválida: '{nature}'. Valores válidos: {sorted(VALID_NATURES)}.", 400
        )

    nickname = (body.get("nickname") or "").strip() or None
    if nickname and len(nickname) > 12:
        return error_response("'nickname' no puede superar 12 caracteres.", 400)

    ability = (body.get("ability") or "").strip() or None

    # IVs — defaults a 31 en todos los stats si no se proveen
    raw_ivs = body.get("ivs", {})
    ivs = {stat: raw_ivs.get(stat, 31) for stat in ALL_STATS}
    iv_errors = validate_ivs(ivs)
    if iv_errors:
        return error_response(f"IVs inválidos: {'; '.join(iv_errors)}", 400)

    # EVs — defaults a 0
    raw_evs = body.get("evs", {})
    evs = {stat: raw_evs.get(stat, 0) for stat in ALL_STATS}
    ev_errors = validate_evs(evs)
    if ev_errors:
        return error_response(f"EVs inválidos: {'; '.join(ev_errors)}", 400)

    # Movimientos
    moves = body.get("moves", [None, None, None, None])
    move_errors = validate_moves(moves)
    if move_errors:
        return error_response(f"Movimientos inválidos: {'; '.join(move_errors)}", 400)

    # 4. Consultar stats base al pokedex-service ← llamada inter-servicio
    log.info("Consultando stats base para pokedex_id=%d al pokedex-service…", pokedex_id)
    pokedex_data, fetch_error = fetch_base_stats(pokedex_id)
    if fetch_error:
        return error_response(f"Error al obtener datos del Pokédex: {fetch_error}", 502)

    base_stats   = pokedex_data["base_stats"]
    species_name = pokedex_data["name"]

    # 5. Calcular stats finales con fórmula Gen 9
    computed_stats = compute_all_stats(base_stats, ivs, evs, level, nature)
    log.info(
        "Stats calculados para %s (Lv%d, %s): %s",
        species_name, level, nature, computed_stats,
    )

    # 6. Determinar el slot libre más bajo
    occupied = {
        r[0] for r in db.execute(
            "SELECT slot FROM pokemon_instances WHERE player_id=?", (player_id,)
        ).fetchall()
    }
    slot = next(s for s in range(1, MAX_TEAM_SIZE + 1) if s not in occupied)

    # 7. Persistir la instancia
    cur = db.execute("""
        INSERT INTO pokemon_instances
            (player_id, pokedex_id, species_name, nickname, level, nature, ability,
             moves_json, ivs_json, evs_json, computed_stats_json, slot)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        RETURNING *
    """, (
        player_id, pokedex_id, species_name, nickname, level, nature, ability,
        json.dumps(moves),
        json.dumps(ivs),
        json.dumps(evs),
        json.dumps(computed_stats),
        slot,
    ))
    instance = instance_row_to_dict(cur.fetchone())
    db.commit()

    return jsonify({
        "success": True,
        "message": f"{species_name} añadido al equipo en el slot {slot}.",
        "data": instance,
    }), 201


@app.route("/team/<int:player_id>/remove/<int:slot>", methods=["DELETE"])
def remove_pokemon(player_id: int, slot: int):
    """DELETE /team/<player_id>/remove/<slot>  — Libera un slot del equipo."""
    if not (1 <= slot <= MAX_TEAM_SIZE):
        return error_response(f"'slot' debe estar entre 1 y {MAX_TEAM_SIZE}.", 400)

    db  = get_db()
    cur = db.execute(
        "DELETE FROM pokemon_instances WHERE player_id=? AND slot=?",
        (player_id, slot),
    )
    db.commit()

    if cur.rowcount == 0:
        return error_response(f"No hay Pokémon en el slot {slot} del jugador {player_id}.", 404)

    return jsonify({
        "success": True,
        "message": f"Slot {slot} liberado del equipo del jugador {player_id}.",
    })


# ---------------------------------------------------------------------------
# Health-check
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    try:
        get_db().execute("SELECT 1")
        return jsonify({"success": True, "service": "team-service", "db": "ok"})
    except Exception as exc:
        log.error("Health-check fallido: %s", exc)
        return jsonify({"success": False, "service": "team-service", "db": "error"}), 503


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    log.info("Iniciando team-service en el puerto 5002…")
    app.run(host="0.0.0.0", port=5002, debug=False)