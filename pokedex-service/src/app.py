# pokemon-arena-microservices/pokedex-service/src/app.py

import sqlite3
import os
import json
import logging
from flask import Flask, jsonify, request, g
from flask_cors import CORS

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [pokedex-service] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

DB_DIR  = os.path.join(os.path.dirname(__file__), "data")
DB_PATH = os.path.join(DB_DIR, "pokedex.db")

# ---------------------------------------------------------------------------
# Utilidades de base de datos
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    """Devuelve la conexión de BD asociada al contexto de la petición actual."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row          # acceso por nombre de columna
        g.db.execute("PRAGMA journal_mode=WAL")  # lecturas concurrentes seguras
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """
    Crea las tablas y carga los datos semilla si la BD está vacía.
    Se llama UNA SOLA VEZ al arrancar el contenedor.
    """
    os.makedirs(DB_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    # ------------------------------------------------------------------
    # Tabla: species
    #
    # Columnas "future-proof":
    #   - types: JSON array de 2 elementos (segundo puede ser null)
    #   - egg_groups, abilities: JSON arrays extensibles
    #   - stats almacenados como columnas individuales para queries rápidas
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS species (
            id          INTEGER PRIMARY KEY,
            name        TEXT    NOT NULL UNIQUE,
            types       TEXT    NOT NULL DEFAULT '[]',   -- JSON: ["Fire", null]
            hp          INTEGER NOT NULL DEFAULT 0,
            attack      INTEGER NOT NULL DEFAULT 0,
            defense     INTEGER NOT NULL DEFAULT 0,
            sp_attack   INTEGER NOT NULL DEFAULT 0,
            sp_defense  INTEGER NOT NULL DEFAULT 0,
            speed       INTEGER NOT NULL DEFAULT 0,
            base_exp    INTEGER NOT NULL DEFAULT 0,
            height_dm   INTEGER NOT NULL DEFAULT 0,      -- decímetros
            weight_hg   INTEGER NOT NULL DEFAULT 0,      -- hectogramos
            abilities   TEXT    NOT NULL DEFAULT '[]',   -- JSON array
            egg_groups  TEXT    NOT NULL DEFAULT '[]',   -- JSON array
            sprite_url  TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # Tabla: moves
    #
    # pp_current se gestiona en el battle-service (estado de combate),
    # aquí sólo guardamos el pp_max (datos de especie).
    # targets: JSON array (future-proof para movimientos multi-objetivo)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS moves (
            id           INTEGER PRIMARY KEY,
            name         TEXT    NOT NULL UNIQUE,
            type         TEXT    NOT NULL,
            category     TEXT    NOT NULL CHECK(category IN ('Physical','Special','Status')),
            power        INTEGER,                        -- NULL para Status
            accuracy     INTEGER,                       -- NULL para always-hit
            pp_max       INTEGER NOT NULL DEFAULT 5,
            priority     INTEGER NOT NULL DEFAULT 0,
            targets      TEXT    NOT NULL DEFAULT '["selected-pokemon"]', -- JSON
            effect_text  TEXT,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    # ------------------------------------------------------------------
    # Tabla: species_moves  (learnset base — movimientos que puede aprender)
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS species_moves (
            species_id  INTEGER NOT NULL REFERENCES species(id),
            move_id     INTEGER NOT NULL REFERENCES moves(id),
            learn_method TEXT NOT NULL DEFAULT 'level-up',
            level_learned INTEGER,
            PRIMARY KEY (species_id, move_id)
        )
    """)

    # ------------------------------------------------------------------
    # Datos semilla — sólo inserta si la tabla está vacía
    # ------------------------------------------------------------------
    if cur.execute("SELECT COUNT(*) FROM species").fetchone()[0] == 0:
        log.info("Insertando datos semilla en species...")

        species_seed = [
            # (id, name, types_json, hp, atk, def, spa, spd, spe, base_exp,
            #  height_dm, weight_hg, abilities_json, egg_groups_json, sprite_url)
            (
                6,
                "Charizard",
                json.dumps(["Fire", "Flying"]),
                78, 84, 78, 109, 85, 100,
                267, 17, 905,
                json.dumps(["Blaze", "Solar Power"]),
                json.dumps(["Monster", "Dragon"]),
                "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/6.png",
            ),
            (
                9,
                "Blastoise",
                json.dumps(["Water", None]),   # mono-tipo: segundo elemento null
                79, 83, 100, 85, 105, 78,
                265, 16, 855,
                json.dumps(["Torrent", "Rain Dish"]),
                json.dumps(["Monster", "Water 1"]),
                "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/9.png",
            ),
        ]

        cur.executemany("""
            INSERT INTO species
                (id, name, types, hp, attack, defense, sp_attack, sp_defense,
                 speed, base_exp, height_dm, weight_hg, abilities, egg_groups, sprite_url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, species_seed)

    if cur.execute("SELECT COUNT(*) FROM moves").fetchone()[0] == 0:
        log.info("Insertando datos semilla en moves...")

        moves_seed = [
            # (id, name, type, category, power, accuracy, pp_max, priority,
            #  targets_json, effect_text)
            (
                53,
                "Flamethrower",
                "Fire", "Special",
                90, 100, 15, 0,
                json.dumps(["selected-pokemon"]),
                "Has a 10% chance to burn the target.",
            ),
            (
                6,
                "Fly",
                "Flying", "Physical",
                90, 95, 15, 0,
                json.dumps(["selected-pokemon"]),
                "User flies up on first turn, then strikes the next. Can hit Pokémon using Bounce or Sky Drop.",
            ),
            (
                55,
                "Surf",
                "Water", "Special",
                90, 100, 15, 0,
                json.dumps(["all-adjacent"]),
                "Hits all adjacent Pokémon. Can hit targets using Dive.",
            ),
            (
                56,
                "Ice Beam",
                "Ice", "Special",
                90, 100, 10, 0,
                json.dumps(["selected-pokemon"]),
                "Has a 10% chance to freeze the target.",
            ),
        ]

        cur.executemany("""
            INSERT INTO moves
                (id, name, type, category, power, accuracy, pp_max, priority,
                 targets, effect_text)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, moves_seed)

    # ------------------------------------------------------------------
    # Learnset semilla
    # ------------------------------------------------------------------
    if cur.execute("SELECT COUNT(*) FROM species_moves").fetchone()[0] == 0:
        log.info("Insertando learnset semilla en species_moves...")

        learnset_seed = [
            # Charizard
            (6, 53, "level-up", 1),   # Flamethrower
            (6,  6, "level-up", 1),   # Fly
            # Blastoise
            (9, 55, "level-up", 1),   # Surf
            (9, 56, "level-up", 1),   # Ice Beam
        ]

        cur.executemany("""
            INSERT INTO species_moves (species_id, move_id, learn_method, level_learned)
            VALUES (?,?,?,?)
        """, learnset_seed)

    conn.commit()
    conn.close()
    log.info("Base de datos inicializada correctamente en %s", DB_PATH)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def species_row_to_dict(row: sqlite3.Row) -> dict:
    """
    Convierte una fila de 'species' a dict serializable.
    Deserializa campos JSON y aplica convenciones future-proof.
    """
    d = dict(row)
    d["types"]      = json.loads(d["types"])       # ["Fire", "Flying"]
    d["abilities"]  = json.loads(d["abilities"])
    d["egg_groups"] = json.loads(d["egg_groups"])
    d["base_stats"] = {
        "hp":         d.pop("hp"),
        "attack":     d.pop("attack"),
        "defense":    d.pop("defense"),
        "sp_attack":  d.pop("sp_attack"),
        "sp_defense": d.pop("sp_defense"),
        "speed":      d.pop("speed"),
    }
    return d


def move_row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["targets"] = json.loads(d["targets"])
    return d


def error_response(message: str, status: int = 400):
    return jsonify({"success": False, "error": message}), status


# ---------------------------------------------------------------------------
# Rutas — Species
# ---------------------------------------------------------------------------

@app.route("/pokedex", methods=["GET"])
def list_species():
    """
    GET /pokedex
    Query params:
      - type    (str)  : filtrar por tipo (ej. ?type=Fire)
      - limit   (int)  : máx resultados (default 20)
      - offset  (int)  : paginación (default 0)
    """
    type_filter = request.args.get("type")
    try:
        limit  = max(1, min(int(request.args.get("limit",  20)), 100))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return error_response("'limit' y 'offset' deben ser enteros.", 400)

    db = get_db()

    if type_filter:
        # Búsqueda dentro del JSON array de tipos
        rows = db.execute("""
            SELECT * FROM species
            WHERE json_each.value = ?
              AND json_each.key IN (SELECT key FROM json_each(types))
            LIMIT ? OFFSET ?
        """, (type_filter, limit, offset)).fetchall()

        # Fallback compatible con SQLite < 3.38 (sin json_each en WHERE directo)
        if not rows:
            all_rows = db.execute("SELECT * FROM species").fetchall()
            rows = [
                r for r in all_rows
                if type_filter in json.loads(r["types"])
            ][offset: offset + limit]
    else:
        rows = db.execute(
            "SELECT * FROM species LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()

    total = db.execute("SELECT COUNT(*) FROM species").fetchone()[0]

    return jsonify({
        "success": True,
        "meta": {"total": total, "limit": limit, "offset": offset},
        "data": [species_row_to_dict(r) for r in rows],
    })


@app.route("/pokedex/<int:species_id>", methods=["GET"])
def get_species(species_id: int):
    """GET /pokedex/<id>  — Datos completos de una especie + su learnset."""
    db = get_db()
    row = db.execute("SELECT * FROM species WHERE id = ?", (species_id,)).fetchone()

    if row is None:
        return error_response(f"Especie con id={species_id} no encontrada.", 404)

    species = species_row_to_dict(row)

    # Learnset asociado
    move_rows = db.execute("""
        SELECT m.*, sm.learn_method, sm.level_learned
        FROM moves m
        JOIN species_moves sm ON sm.move_id = m.id
        WHERE sm.species_id = ?
        ORDER BY sm.level_learned
    """, (species_id,)).fetchall()

    species["learnset"] = [move_row_to_dict(r) for r in move_rows]

    return jsonify({"success": True, "data": species})


@app.route("/pokedex/name/<string:name>", methods=["GET"])
def get_species_by_name(name: str):
    """GET /pokedex/name/<name>  — Búsqueda por nombre (case-insensitive)."""
    db = get_db()
    row = db.execute(
        "SELECT * FROM species WHERE LOWER(name) = LOWER(?)", (name,)
    ).fetchone()

    if row is None:
        return error_response(f"Especie '{name}' no encontrada.", 404)

    species = species_row_to_dict(row)
    return jsonify({"success": True, "data": species})


# ---------------------------------------------------------------------------
# Rutas — Moves
# ---------------------------------------------------------------------------

@app.route("/moves", methods=["GET"])
def list_moves():
    """
    GET /moves
    Query params:
      - type     (str): filtrar por tipo
      - category (str): Physical | Special | Status
      - limit    (int): default 20
      - offset   (int): default 0
    """
    type_filter     = request.args.get("type")
    category_filter = request.args.get("category")
    try:
        limit  = max(1, min(int(request.args.get("limit",  20)), 100))
        offset = max(0, int(request.args.get("offset", 0)))
    except ValueError:
        return error_response("'limit' y 'offset' deben ser enteros.", 400)

    query  = "SELECT * FROM moves WHERE 1=1"
    params: list = []

    if type_filter:
        query += " AND LOWER(type) = LOWER(?)"
        params.append(type_filter)

    if category_filter:
        valid = {"Physical", "Special", "Status"}
        if category_filter not in valid:
            return error_response(f"'category' debe ser uno de {valid}.", 400)
        query += " AND category = ?"
        params.append(category_filter)

    db = get_db()
    total_query = query.replace("SELECT *", "SELECT COUNT(*)")
    total = db.execute(total_query, params).fetchone()[0]

    query += " LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = db.execute(query, params).fetchall()

    return jsonify({
        "success": True,
        "meta": {"total": total, "limit": limit, "offset": offset},
        "data": [move_row_to_dict(r) for r in rows],
    })


@app.route("/moves/<int:move_id>", methods=["GET"])
def get_move(move_id: int):
    """GET /moves/<id>"""
    db  = get_db()
    row = db.execute("SELECT * FROM moves WHERE id = ?", (move_id,)).fetchone()

    if row is None:
        return error_response(f"Movimiento con id={move_id} no encontrado.", 404)

    return jsonify({"success": True, "data": move_row_to_dict(row)})


# ---------------------------------------------------------------------------
# Health-check (usado por el API Gateway y Docker healthchecks)
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    try:
        db = get_db()
        db.execute("SELECT 1")
        return jsonify({"success": True, "service": "pokedex-service", "db": "ok"})
    except Exception as exc:
        log.error("Health-check fallido: %s", exc)
        return jsonify({"success": False, "service": "pokedex-service", "db": "error"}), 503


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    log.info("Iniciando pokedex-service en el puerto 5001…")
    app.run(host="0.0.0.0", port=5001, debug=False)