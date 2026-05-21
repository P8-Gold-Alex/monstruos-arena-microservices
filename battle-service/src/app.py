# pokemon-arena-microservices/battle-service/src/app.py

import math
import os
import json
import uuid
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
    format="%(asctime)s [battle-service] %(levelname)s: %(message)s",
)
log = logging.getLogger(__name__)

DB_DIR   = os.path.join(os.path.dirname(__file__), "data")
DB_PATH  = os.path.join(DB_DIR, "battles.db")

TEAM_URL     = os.getenv("TEAM_SERVICE_URL", "http://team-service:5002")
HTTP_TIMEOUT = 5

# ---------------------------------------------------------------------------
# Estados de la partida
# ---------------------------------------------------------------------------

class BattleState:
    WAITING_ACTIONS = "waiting_actions"  # esperando ≥1 acción
    RESOLVING       = "resolving"        # procesando turno (lock anti-race)
    FINISHED        = "finished"         # un equipo fue derrotado

class ActionType:
    ATTACK = "attack"
    SWITCH = "switch"   # preparado para Fase 4
    # FORFEIT = "forfeit"  # futuro

VALID_ACTIONS = {ActionType.ATTACK, ActionType.SWITCH}

# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------

import sqlite3

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
    cur = conn.cursor()

    # ------------------------------------------------------------------
    # active_battles
    #
    # Columnas future-proof:
    #   p1_team_json / p2_team_json : snapshot completo del equipo al crear
    #                                 la partida (stats congelados).
    #   pending_actions_json        : {"p1": {...}|null, "p2": {...}|null}
    #   turn_log_json               : array de turnos resueltos (historial)
    #   weather_json                : null → reservado para efectos de clima
    #   field_json                  : null → reservado para efectos de campo
    # ------------------------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS active_battles (
            id                   TEXT    PRIMARY KEY,
            player1_id           INTEGER NOT NULL,
            player2_id           INTEGER NOT NULL,
            battle_state         TEXT    NOT NULL DEFAULT 'waiting_actions',
            turn_number          INTEGER NOT NULL DEFAULT 1,
            p1_active_slot       INTEGER NOT NULL DEFAULT 0,
            p2_active_slot       INTEGER NOT NULL DEFAULT 0,
            p1_team_json         TEXT    NOT NULL DEFAULT '[]',
            p2_team_json         TEXT    NOT NULL DEFAULT '[]',
            pending_actions_json TEXT    NOT NULL DEFAULT '{"p1":null,"p2":null}',
            turn_log_json        TEXT    NOT NULL DEFAULT '[]',
            weather_json         TEXT,
            field_json           TEXT,
            winner_player_id     INTEGER,
            created_at           TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at           TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    log.info("battles.db inicializada en %s", DB_PATH)


# ---------------------------------------------------------------------------
# Helpers de BD
# ---------------------------------------------------------------------------

def get_battle(battle_id: str) -> dict | None:
    row = get_db().execute(
        "SELECT * FROM active_battles WHERE id=?", (battle_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["p1_team"]         = json.loads(d.pop("p1_team_json"))
    d["p2_team"]         = json.loads(d.pop("p2_team_json"))
    d["pending_actions"] = json.loads(d.pop("pending_actions_json"))
    d["turn_log"]        = json.loads(d.pop("turn_log_json"))
    return d


def save_battle(battle: dict):
    """Persiste el estado mutable de una partida."""
    get_db().execute("""
        UPDATE active_battles SET
            battle_state         = ?,
            turn_number          = ?,
            p1_active_slot       = ?,
            p2_active_slot       = ?,
            p1_team_json         = ?,
            p2_team_json         = ?,
            pending_actions_json = ?,
            turn_log_json        = ?,
            winner_player_id     = ?,
            updated_at           = datetime('now')
        WHERE id = ?
    """, (
        battle["battle_state"],
        battle["turn_number"],
        battle["p1_active_slot"],
        battle["p2_active_slot"],
        json.dumps(battle["p1_team"]),
        json.dumps(battle["p2_team"]),
        json.dumps(battle["pending_actions"]),
        json.dumps(battle["turn_log"]),
        battle.get("winner_player_id"),
        battle["id"],
    ))
    get_db().commit()


def error_response(msg: str, status: int = 400):
    return jsonify({"success": False, "error": msg}), status


# ---------------------------------------------------------------------------
# Llamada inter-servicio al team-service
# ---------------------------------------------------------------------------

def fetch_team(player_id: int) -> tuple[list | None, str | None]:
    """
    Obtiene el equipo de un jugador desde el team-service.
    Devuelve (team_list, None) o (None, error_message).
    """
    url = f"{TEAM_URL}/team/{player_id}"
    try:
        resp = http.get(url, timeout=HTTP_TIMEOUT)
    except http.exceptions.ConnectionError:
        return None, f"No se pudo conectar al team-service ({TEAM_URL})."
    except http.exceptions.Timeout:
        return None, f"team-service tardó más de {HTTP_TIMEOUT}s."
    except http.exceptions.RequestException as exc:
        return None, f"Error al contactar team-service: {exc}"

    if resp.status_code == 404:
        return None, f"Jugador id={player_id} no existe en el team-service."
    if not resp.ok:
        return None, f"team-service respondió HTTP {resp.status_code}: {resp.text[:200]}"

    try:
        data = resp.json()["data"]          # array de 6 (Pokémon | null)
    except (KeyError, ValueError) as exc:
        return None, f"Respuesta inesperada del team-service: {exc}"

    # Filtrar slots vacíos (null) y verificar que haya al menos 1 Pokémon
    team = [p for p in data if p is not None]
    if not team:
        return None, f"El jugador id={player_id} no tiene Pokémon en su equipo."

    return team, None


# ---------------------------------------------------------------------------
# Motor de combate — fórmulas
# ---------------------------------------------------------------------------

def damage_formula(
    level: int,
    power: int,
    attack_stat: int,
    defense_stat: int,
) -> int:
    """
    Fórmula oficial Gen 9 (versión simplificada sin modificadores de campo):

        Daño = floor( floor( floor(2×Nivel/5 + 2) × Poder × Atk/Def ) / 50 ) + 2

    Resultado mínimo garantizado: 1 (never-miss floor).
    """
    step1 = math.floor(2 * level / 5 + 2)
    step2 = math.floor(step1 * power * attack_stat / defense_stat)
    step3 = math.floor(step2 / 50) + 2
    return max(1, step3)


def resolve_attack(
    attacker: dict,
    defender: dict,
    move_id: int,
    move_category_override: str | None = None,
) -> dict:
    """
    Calcula el daño de un ataque y actualiza el HP del defensor en-place.

    El movimiento se identifica por move_id dentro de attacker["moves"].
    Si no existe o su power es None/0 (Status), devuelve daño 0.

    Returns un dict con el log del ataque.
    """
    # Localizar el movimiento en el slot del atacante
    move_data = next(
        (m for m in attacker.get("_move_details", []) if m and m.get("id") == move_id),
        None,
    )

    # Fallback: si no tenemos detalles, calculamos con power=60 (promedio)
    if move_data is None:
        move_data = {
            "id": move_id, "name": f"Move#{move_id}",
            "category": move_category_override or "Physical",
            "power": 60, "type": "Normal",
        }

    power    = move_data.get("power") or 0
    category = move_data.get("category", "Physical")
    move_name = move_data.get("name", f"Move#{move_id}")

    atk_stats = attacker["computed_stats"]
    def_stats = defender["computed_stats"]

    log_entry: dict = {
        "attacker": attacker["species_name"],
        "move_name": move_name,
        "move_id": move_id,
        "category": category,
        "power": power,
    }

    if power == 0:
        log_entry.update({"damage": 0, "effect": "status_move_used"})
        return log_entry

    # Elegir stats correctos según categoría
    if category == "Special":
        atk_val = atk_stats["sp_attack"]
        def_val = def_stats["sp_defense"]
    else:  # Physical (y cualquier desconocido)
        atk_val = atk_stats["attack"]
        def_val = def_stats["defense"]

    damage = damage_formula(
        level=attacker["level"],
        power=power,
        attack_stat=atk_val,
        defense_stat=def_val,
    )

    # Aplicar daño (el HP no puede bajar de 0)
    prev_hp = defender["computed_stats"]["hp"]
    defender["computed_stats"]["hp"] = max(0, prev_hp - damage)
    new_hp  = defender["computed_stats"]["hp"]

    log_entry.update({
        "damage": damage,
        "target": defender["species_name"],
        "target_hp_before": prev_hp,
        "target_hp_after":  new_hp,
        "fainted": new_hp == 0,
    })
    return log_entry


def determine_order(
    p1_pokemon: dict,
    p2_pokemon: dict,
    p1_action: dict,
    p2_action: dict,
) -> tuple[str, str]:
    """
    Determina qué jugador actúa primero.
    Reglas por prioridad (de mayor a menor):
      1. Cambio de Pokémon (switch) siempre va antes que ataque.
      2. Mayor speed del Pokémon activo.
      3. Empate de speed → aleatoriedad determinista (player1 gana el tie por defecto,
         se puede extender con random para producción).
    Returns ("p1", "p2") o ("p2", "p1").
    """
    p1_type = p1_action.get("action")
    p2_type = p2_action.get("action")

    if p1_type == ActionType.SWITCH and p2_type != ActionType.SWITCH:
        return "p1", "p2"
    if p2_type == ActionType.SWITCH and p1_type != ActionType.SWITCH:
        return "p2", "p1"

    p1_speed = p1_pokemon["computed_stats"]["speed"]
    p2_speed = p2_pokemon["computed_stats"]["speed"]

    if p1_speed >= p2_speed:
        return "p1", "p2"
    return "p2", "p1"


def find_first_alive_slot(team: list) -> int | None:
    """Devuelve el índice (0-based) del primer Pokémon vivo, o None si todos fainted."""
    for i, poke in enumerate(team):
        if poke is not None and poke["computed_stats"]["hp"] > 0:
            return i
    return None


def check_team_defeated(team: list) -> bool:
    return all(
        p is None or p["computed_stats"]["hp"] <= 0
        for p in team
    )


# ---------------------------------------------------------------------------
# Resolución de turno completo
# ---------------------------------------------------------------------------

def resolve_turn(battle: dict) -> dict:
    """
    Ejecuta las acciones de ambos jugadores, actualiza el estado del battle
    en-place y devuelve el log del turno listo para devolver al cliente.
    """
    p1_team  = battle["p1_team"]
    p2_team  = battle["p2_team"]
    p1_slot  = battle["p1_active_slot"]
    p2_slot  = battle["p2_active_slot"]
    p1_poke  = p1_team[p1_slot]
    p2_poke  = p2_team[p2_slot]
    p1_act   = battle["pending_actions"]["p1"]
    p2_act   = battle["pending_actions"]["p2"]

    turn_events: list[dict] = []
    fainted_flags: dict     = {"p1": False, "p2": False}

    # --- Determinar orden de actuación ---
    first, second = determine_order(p1_poke, p2_poke, p1_act, p2_act)

    ordered = [
        ("p1" if first  == "p1" else "p2",
         p1_poke if first  == "p1" else p2_poke,
         p2_poke if first  == "p1" else p1_poke,
         p1_act  if first  == "p1" else p2_act),
        ("p1" if second == "p1" else "p2",
         p1_poke if second == "p1" else p2_poke,
         p2_poke if second == "p1" else p1_poke,
         p1_act  if second == "p1" else p2_act),
    ]

    for actor_key, attacker, defender, action in ordered:
        defender_key = "p2" if actor_key == "p1" else "p1"

        # Si el atacante ya está debilitado, se salta su acción
        if attacker["computed_stats"]["hp"] <= 0:
            turn_events.append({
                "actor": actor_key,
                "skipped": True,
                "reason": "fainted_before_action",
            })
            continue

        if action["action"] == ActionType.ATTACK:
            move_id = action.get("move_id")
            if move_id is None:
                turn_events.append({
                    "actor": actor_key,
                    "error": "move_id faltante en la acción de ataque.",
                })
                continue

            event = resolve_attack(attacker, defender, move_id)
            event["actor"] = actor_key
            turn_events.append(event)

            if event.get("fainted"):
                fainted_flags[defender_key] = True
                log.info(
                    "%s fue debilitado por %s (Turno %d).",
                    defender["species_name"], attacker["species_name"],
                    battle["turn_number"],
                )

        elif action["action"] == ActionType.SWITCH:
            new_slot = action.get("slot")
            team     = p1_team if actor_key == "p1" else p2_team

            # Validar el nuevo slot
            if (
                new_slot is None
                or not isinstance(new_slot, int)
                or not (0 <= new_slot < len(team))
                or team[new_slot] is None
                or team[new_slot]["computed_stats"]["hp"] <= 0
            ):
                turn_events.append({
                    "actor": actor_key,
                    "error": f"Slot de cambio inválido o Pokémon debilitado: {new_slot}.",
                })
                continue

            old_name = attacker["species_name"]
            if actor_key == "p1":
                battle["p1_active_slot"] = new_slot
            else:
                battle["p2_active_slot"] = new_slot

            turn_events.append({
                "actor":    actor_key,
                "action":   "switch",
                "from":     old_name,
                "to":       team[new_slot]["species_name"],
                "new_slot": new_slot,
            })

    # --- Post-turno: auto-switch si el Pokémon activo fue debilitado ---
    post_events: list[dict] = []
    for player_key, team_key in [("p1", p1_team), ("p2", p2_team)]:
        slot_attr = f"{player_key}_active_slot"
        current   = battle[slot_attr]
        if team_key[current]["computed_stats"]["hp"] <= 0:
            next_slot = find_first_alive_slot(team_key)
            if next_slot is not None:
                battle[slot_attr] = next_slot
                post_events.append({
                    "event":    "auto_switch",
                    "player":   player_key,
                    "new_slot": next_slot,
                    "new_pokemon": team_key[next_slot]["species_name"],
                })

    # --- Verificar fin de partida ---
    p1_defeated = check_team_defeated(p1_team)
    p2_defeated = check_team_defeated(p2_team)

    if p1_defeated or p2_defeated:
        battle["battle_state"] = BattleState.FINISHED
        if p2_defeated and not p1_defeated:
            battle["winner_player_id"] = battle["player1_id"]
        elif p1_defeated and not p2_defeated:
            battle["winner_player_id"] = battle["player2_id"]
        else:
            battle["winner_player_id"] = None  # empate simultáneo
    else:
        battle["battle_state"] = BattleState.WAITING_ACTIONS

    # --- Limpiar acciones pendientes y avanzar turno ---
    completed_turn = battle["turn_number"]
    battle["pending_actions"] = {"p1": None, "p2": None}
    battle["turn_number"]    += 1

    # Construir log del turno
    turn_record = {
        "turn":        completed_turn,
        "events":      turn_events,
        "post_events": post_events,
        "p1_hp_snapshot": {
            p["species_name"]: p["computed_stats"]["hp"]
            for p in p1_team if p is not None
        },
        "p2_hp_snapshot": {
            p["species_name"]: p["computed_stats"]["hp"]
            for p in p2_team if p is not None
        },
    }
    battle["turn_log"].append(turn_record)

    return turn_record


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

@app.route("/battle/create", methods=["POST"])
def create_battle():
    """
    POST /battle/create
    Body: { "player1_id": 1, "player2_id": 2 }

    1. Valida jugadores distintos.
    2. Obtiene equipos desde el team-service (snapshot congelado).
    3. Crea la partida en battles.db y devuelve el battle_id.
    """
    body = request.get_json(silent=True) or {}
    p1_id = body.get("player1_id")
    p2_id = body.get("player2_id")

    if not isinstance(p1_id, int) or not isinstance(p2_id, int):
        return error_response("'player1_id' y 'player2_id' deben ser enteros.", 400)
    if p1_id == p2_id:
        return error_response("Un jugador no puede batallar contra sí mismo.", 400)

    log.info("Creando batalla entre jugadores %d y %d…", p1_id, p2_id)

    # Obtener equipos (snapshot)
    p1_team, err1 = fetch_team(p1_id)
    if err1:
        return error_response(f"Error al obtener equipo del jugador 1: {err1}", 502)

    p2_team, err2 = fetch_team(p2_id)
    if err2:
        return error_response(f"Error al obtener equipo del jugador 2: {err2}", 502)

    battle_id = str(uuid.uuid4())

    db = get_db()
    db.execute("""
        INSERT INTO active_battles
            (id, player1_id, player2_id, p1_team_json, p2_team_json)
        VALUES (?, ?, ?, ?, ?)
    """, (battle_id, p1_id, p2_id, json.dumps(p1_team), json.dumps(p2_team)))
    db.commit()

    log.info("Batalla %s creada. P1=%d (%d Pkm) vs P2=%d (%d Pkm).",
             battle_id, p1_id, len(p1_team), p2_id, len(p2_team))

    return jsonify({
        "success":   True,
        "battle_id": battle_id,
        "player1_id": p1_id,
        "player2_id": p2_id,
        "p1_lead":   p1_team[0]["species_name"],
        "p2_lead":   p2_team[0]["species_name"],
        "message":   "Batalla creada. ¡Que empiecen los turnos!",
    }), 201


@app.route("/battle/<string:battle_id>", methods=["GET"])
def get_battle_state(battle_id: str):
    """GET /battle/<battle_id>  — Estado actual de la partida."""
    battle = get_battle(battle_id)
    if battle is None:
        return error_response(f"Batalla '{battle_id}' no encontrada.", 404)

    return jsonify({
        "success":      True,
        "data": {
            "id":              battle["id"],
            "battle_state":    battle["battle_state"],
            "turn_number":     battle["turn_number"],
            "player1_id":      battle["player1_id"],
            "player2_id":      battle["player2_id"],
            "p1_active_slot":  battle["p1_active_slot"],
            "p2_active_slot":  battle["p2_active_slot"],
            "p1_active":       battle["p1_team"][battle["p1_active_slot"]]["species_name"],
            "p2_active":       battle["p2_team"][battle["p2_active_slot"]]["species_name"],
            "p1_team_hp": {
                p["species_name"]: p["computed_stats"]["hp"]
                for p in battle["p1_team"] if p is not None
            },
            "p2_team_hp": {
                p["species_name"]: p["computed_stats"]["hp"]
                for p in battle["p2_team"] if p is not None
            },
            "winner_player_id": battle.get("winner_player_id"),
            "turn_log":         battle["turn_log"],
        },
    })


@app.route("/battle/<string:battle_id>/turn", methods=["POST"])
def submit_turn(battle_id: str):
    """
    POST /battle/<battle_id>/turn

    Body:
    {
        "player_id": 1,
        "action":    "attack",   // "attack" | "switch"
        "move_id":   53,         // requerido si action="attack"
        "slot":      2           // requerido si action="switch" (0-based)
    }

    Flujo:
      1. Registra la acción del jugador.
      2. Si falta la del rival → {"status": "waiting"}.
      3. Si ambas están → resolve_turn() y devuelve el log del turno.
    """
    battle = get_battle(battle_id)
    if battle is None:
        return error_response(f"Batalla '{battle_id}' no encontrada.", 404)

    if battle["battle_state"] == BattleState.FINISHED:
        winner = battle.get("winner_player_id")
        return error_response(
            f"La batalla ya ha terminado. Ganador: jugador {winner}.", 409
        )

    if battle["battle_state"] == BattleState.RESOLVING:
        return error_response(
            "El turno está siendo procesado. Reintenta en un momento.", 409
        )

    body = request.get_json(silent=True) or {}
    player_id = body.get("player_id")
    action    = body.get("action")

    # --- Validar player_id ---
    if player_id not in (battle["player1_id"], battle["player2_id"]):
        return error_response(
            f"player_id={player_id} no participa en esta batalla.", 403
        )

    # --- Determinar clave del jugador (p1 / p2) ---
    player_key = "p1" if player_id == battle["player1_id"] else "p2"

    # --- Verificar que no haya enviado ya su acción este turno ---
    if battle["pending_actions"][player_key] is not None:
        return error_response(
            f"El jugador {player_id} ya envió su acción para el turno {battle['turn_number']}.",
            409,
        )

    # --- Validar tipo de acción ---
    if action not in VALID_ACTIONS:
        return error_response(
            f"'action' inválida: '{action}'. Valores válidos: {sorted(VALID_ACTIONS)}.", 400
        )

    if action == ActionType.ATTACK:
        move_id = body.get("move_id")
        if not isinstance(move_id, int):
            return error_response("'move_id' es obligatorio (entero) para action='attack'.", 400)

        # Verificar que el movimiento pertenece al Pokémon activo
        team_key = f"{player_key}_team"
        slot_key = f"{player_key}_active_slot"
        active_moves = battle[team_key][battle[slot_key]].get("moves", [])

        if move_id not in active_moves:
            return error_response(
                f"move_id={move_id} no pertenece al Pokémon activo "
                f"({battle[team_key][battle[slot_key]]['species_name']}). "
                f"Movimientos disponibles: {active_moves}",
                400,
            )
        action_data = {"action": ActionType.ATTACK, "move_id": move_id}

    else:  # SWITCH
        slot = body.get("slot")
        if not isinstance(slot, int):
            return error_response("'slot' es obligatorio (entero 0-based) para action='switch'.", 400)
        action_data = {"action": ActionType.SWITCH, "slot": slot}

    # --- Guardar acción ---
    battle["pending_actions"][player_key] = action_data
    rival_key = "p2" if player_key == "p1" else "p1"

    # Sólo persistimos acciones pendientes (no el estado completo aún)
    get_db().execute(
        "UPDATE active_battles SET pending_actions_json=?, updated_at=datetime('now') WHERE id=?",
        (json.dumps(battle["pending_actions"]), battle_id),
    )
    get_db().commit()

    # --- ¿Falta la acción del rival? ---
    if battle["pending_actions"][rival_key] is None:
        log.info(
            "Batalla %s turno %d: acción de %s registrada. Esperando al rival.",
            battle_id, battle["turn_number"], player_key,
        )
        return jsonify({
            "success": True,
            "status":  "waiting",
            "message": f"Acción registrada para jugador {player_id}. Esperando al rival.",
            "turn":    battle["turn_number"],
        })

    # --- Ambas acciones presentes → resolver turno ---
    log.info(
        "Batalla %s turno %d: ambas acciones recibidas. Resolviendo…",
        battle_id, battle["turn_number"],
    )
    battle["battle_state"] = BattleState.RESOLVING
    save_battle(battle)   # Lock anti-race: cualquier petición concurrente
                          # verá estado RESOLVING y recibirá 409.

    try:
        turn_log = resolve_turn(battle)
    except Exception as exc:
        # Revertir lock si algo falla inesperadamente
        battle["battle_state"] = BattleState.WAITING_ACTIONS
        save_battle(battle)
        log.exception("Error inesperado al resolver turno: %s", exc)
        return error_response(f"Error interno al resolver el turno: {exc}", 500)

    save_battle(battle)

    # --- Construir respuesta ---
    response_body: dict = {
        "success":      True,
        "status":       "turn_resolved",
        "turn":         turn_log["turn"],
        "events":       turn_log["events"],
        "post_events":  turn_log["post_events"],
        "p1_hp_snapshot": turn_log["p1_hp_snapshot"],
        "p2_hp_snapshot": turn_log["p2_hp_snapshot"],
    }

    if battle["battle_state"] == BattleState.FINISHED:
        winner_id = battle.get("winner_player_id")
        response_body["battle_over"] = True
        response_body["winner_player_id"] = winner_id
        response_body["message"] = (
            f"¡Batalla terminada! Ganador: jugador {winner_id}."
            if winner_id else "¡Empate simultáneo!"
        )
        log.info("Batalla %s finalizada. Ganador: %s", battle_id, winner_id)
    else:
        response_body["battle_over"] = False
        response_body["next_turn"]   = battle["turn_number"]
        response_body["p1_active"]   = battle["p1_team"][battle["p1_active_slot"]]["species_name"]
        response_body["p2_active"]   = battle["p2_team"][battle["p2_active_slot"]]["species_name"]

    return jsonify(response_body)


# ---------------------------------------------------------------------------
# Health-check
# ---------------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    try:
        get_db().execute("SELECT 1")
        return jsonify({"success": True, "service": "battle-service", "db": "ok"})
    except Exception as exc:
        log.error("Health-check fallido: %s", exc)
        return jsonify({"success": False, "service": "battle-service", "db": "error"}), 503


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    log.info("Iniciando battle-service en el puerto 5003…")
    app.run(host="0.0.0.0", port=5003, debug=False)