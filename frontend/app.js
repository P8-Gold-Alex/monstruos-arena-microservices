// ═══════════════════════════════════════════════════════════════
//  POKÉMON ARENA — app.js
//  SPA que consume el API Gateway en /api/...
//  Uso: todos los fetch pasan por API_BASE (proxy nginx → :5000)
// ═══════════════════════════════════════════════════════════════

const API_BASE = '/api';   // nginx proxea /api/ → api-gateway:5000/api/
// Si abres index.html directamente sin nginx: 'http://localhost:5000/api'

// ─── Naturalezas disponibles (las 25 de Gen 3-9) ───────────────
const NATURES = [
  "Hardy","Lonely","Brave","Adamant","Naughty",
  "Bold","Docile","Relaxed","Impish","Lax",
  "Timid","Hasty","Serious","Jolly","Naive",
  "Modest","Mild","Quiet","Bashful","Rash",
  "Calm","Gentle","Sassy","Careful","Quirky",
];

const STAT_KEYS  = ["hp","attack","defense","sp_attack","sp_defense","speed"];
const STAT_LABEL = { hp:"HP", attack:"ATK", defense:"DEF",
                     sp_attack:"SPA", sp_defense:"SPD", speed:"SPE" };

// ─── Estado global de la aplicación ────────────────────────────
const State = {
  player:          null,   // { id, name }
  pokedex:         [],     // lista de especies del servidor
  team:            [],     // instancias en el equipo del jugador
  selectedDexId:   null,   // pokedex_id seleccionado en el dex para añadir
  battle:          null,   // { id, player1_id, player2_id, turn_number, … }
  selectedMoveId:  null,   // move_id elegido para el turno actual
  waitingForRival: false,  // true si ya enviamos acción y esperamos al rival
};

// ═══════════════════════════════════════════════════════════════
//  UTILIDADES
// ═══════════════════════════════════════════════════════════════

/** Muestra un toast de notificación (2.5 s) */
function showToast(msg, isError = false) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('toast--error', isError);
  t.classList.add('visible');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('visible'), 2500);
}

/** Añade una línea al log de batalla */
function logBattle(msg, type = 'info') {
  const log  = document.getElementById('battleLog');
  const line = document.createElement('p');
  line.className = `log-entry log-entry--${type}`;
  line.textContent = msg;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

/** Cambia la vista activa */
function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  window.scrollTo(0, 0);
}

/** Wrapper de fetch con manejo uniforme de errores */
async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res  = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data?.error || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

/** Calcula el porcentaje de HP para la barra */
function hpPercent(current, max) {
  return Math.max(0, Math.min(100, Math.round((current / max) * 100)));
}

/** Actualiza la barra y texto de HP de un combatiente */
function updateHpBar(prefix, currentHp, maxHp) {
  const bar  = document.getElementById(`${prefix}HpBar`);
  const text = document.getElementById(`${prefix}HpText`);
  if (!bar || !text) return;
  const pct = hpPercent(currentHp, maxHp);
  bar.style.width = `${pct}%`;
  bar.classList.remove('hp--medium', 'hp--low');
  if (pct <= 20)       bar.classList.add('hp--low');
  else if (pct <= 50)  bar.classList.add('hp--medium');
  text.textContent = `${currentHp} / ${maxHp}`;
}

// ═══════════════════════════════════════════════════════════════
//  INICIALIZACIÓN
// ═══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  populateNatureSelect();
  buildEvInputs();
  checkServicesHealth();

  // ── Registro ──────────────────────────────────────────────
  document.getElementById('formRegister')
    .addEventListener('submit', onRegister);

  document.getElementById('btnLoadPokedex')
    .addEventListener('click', () => {
      showView('view-team');
      loadPokedex();
      loadMyTeam();
    });

  // ── Equipo ────────────────────────────────────────────────
  document.getElementById('btnConfirmAdd')
    .addEventListener('click', onConfirmAddPokemon);

  document.getElementById('btnCancelAdd')
    .addEventListener('click', closeAddForm);

  document.getElementById('btnGoToBattle')
    .addEventListener('click', () => {
      document.getElementById('setupP1Name').textContent = State.player.name;
      document.getElementById('setupP1Id').textContent   = `ID: ${State.player.id}`;
      showView('view-setup');
    });

  // ── Setup batalla ─────────────────────────────────────────
  document.getElementById('btnCreateBattle')
    .addEventListener('click', onCreateBattle);

  document.getElementById('btnBackToTeam')
    .addEventListener('click', () => showView('view-team'));

  // ── Batalla ───────────────────────────────────────────────
  document.getElementById('btnSendTurn')
    .addEventListener('click', onSendTurn);

  document.getElementById('btnRefresh')
    .addEventListener('click', onRefreshBattle);
});

// ─── Rellena el <select> de naturalezas ────────────────────────
function populateNatureSelect() {
  const sel = document.getElementById('addNature');
  NATURES.forEach(n => {
    const opt = document.createElement('option');
    opt.value = n;
    opt.textContent = n;
    sel.appendChild(opt);
  });
}

// ─── Construye los inputs de EVs dinámicamente ─────────────────
function buildEvInputs() {
  const container = document.getElementById('evInputs');
  STAT_KEYS.forEach(stat => {
    const wrap  = document.createElement('label');
    wrap.className = 'field';
    wrap.innerHTML = `
      <span class="field__label">${STAT_LABEL[stat]}</span>
      <input id="ev_${stat}" class="field__input" type="number"
             value="0" min="0" max="252" step="4" />
    `;
    container.appendChild(wrap);
  });
}

// ═══════════════════════════════════════════════════════════════
//  HEALTH CHECK
// ═══════════════════════════════════════════════════════════════

async function checkServicesHealth() {
  const el = document.getElementById('svcStatus');
  try {
    const data = await apiFetch('/health');
    const allOk = Object.values(data.services || {})
                        .every(s => s.status === 'ok');
    el.innerHTML = allOk
      ? '<span class="dot dot--ok"></span> todos los servicios OK'
      : '<span class="dot dot--error"></span> algún servicio con problemas';
  } catch {
    el.innerHTML = '<span class="dot dot--error"></span> API Gateway inalcanzable';
  }
}

// ═══════════════════════════════════════════════════════════════
//  VISTA 1 — REGISTRO
// ═══════════════════════════════════════════════════════════════

async function onRegister(e) {
  e.preventDefault();
  const name = document.getElementById('inputPlayerName').value.trim();
  if (!name) return;

  const btn = e.target.querySelector('button[type="submit"]');
  btn.disabled = true;
  btn.textContent = 'REGISTRANDO…';

  try {
    const data = await apiFetch('/players', {
      method: 'POST',
      body:   JSON.stringify({ name }),
    });
    State.player = { id: data.data.id, name: data.data.name };
    document.getElementById('playerNameDisplay').textContent = State.player.name;
    document.getElementById('playerIdDisplay').textContent   = State.player.id;
    document.getElementById('playerInfo').classList.remove('hidden');
    showToast(`¡Bienvenido, ${State.player.name}!`);
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'REGISTRAR';
  }
}

// ═══════════════════════════════════════════════════════════════
//  VISTA 2 — EQUIPO
// ═══════════════════════════════════════════════════════════════

async function loadPokedex() {
  const grid = document.getElementById('pokedexGrid');
  grid.innerHTML = '<p class="muted">Cargando…</p>';
  try {
    const data = await apiFetch('/pokedex?limit=100');
    State.pokedex = data.data || [];
    renderPokedex();
  } catch (err) {
    grid.innerHTML = `<p class="muted">Error: ${err.message}</p>`;
  }
}

function renderPokedex() {
  const grid = document.getElementById('pokedexGrid');
  grid.innerHTML = '';
  State.pokedex.forEach(sp => {
    const card = document.createElement('div');
    card.className  = 'dex-card';
    card.dataset.id = sp.id;
    const types = (sp.types || []).filter(Boolean)
      .map(t => `<span class="type-chip">${t}</span>`).join('');
    card.innerHTML = `
      <img src="${sp.sprite_url || ''}" alt="${sp.name}"
           onerror="this.src='https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/0.png'" />
      <p class="dex-card__name">${sp.name.toUpperCase()}</p>
      <p class="dex-card__id">#${sp.id}</p>
      <div>${types}</div>
    `;
    card.addEventListener('click', () => openAddForm(sp));
    grid.appendChild(card);
  });
}

async function loadMyTeam() {
  if (!State.player) return;
  try {
    const data = await apiFetch(`/team/${State.player.id}`);
    State.team = (data.data || []).filter(Boolean);
    renderTeam();
  } catch (err) {
    showToast(`Error cargando equipo: ${err.message}`, true);
  }
}

function renderTeam() {
  const list  = document.getElementById('teamList');
  const count = document.getElementById('teamCount');
  const btn   = document.getElementById('btnGoToBattle');
  list.innerHTML = '';
  State.team.forEach(pk => {
    const li  = document.createElement('li');
    li.className = 'team-item';
    const hp  = pk.computed_stats?.hp ?? '?';
    const spe = pk.computed_stats?.speed ?? '?';
    li.innerHTML = `
      <span class="team-item__slot">${pk.slot}</span>
      <img src="${pk.sprite_url || getSpriteUrl(pk.pokedex_id)}"
           alt="${pk.species_name}"
           onerror="this.src=''" />
      <div class="team-item__info">
        <p class="team-item__name">${(pk.nickname || pk.species_name).toUpperCase()}</p>
        <p class="team-item__stats">Lv${pk.level} · ${pk.nature} · HP:${hp} SPE:${spe}</p>
      </div>
      <button class="team-item__remove" data-slot="${pk.slot}" title="Quitar">✕</button>
    `;
    li.querySelector('.team-item__remove')
      .addEventListener('click', () => onRemovePokemon(pk.slot));
    list.appendChild(li);
  });
  count.textContent = `${State.team.length}/6`;
  btn.disabled = State.team.length === 0;
}

function getSpriteUrl(pokedexId) {
  return `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/${pokedexId}.png`;
}

// ── Abre el formulario de añadir para una especie ──────────────
function openAddForm(species) {
  State.selectedDexId = species.id;
  const form = document.getElementById('addPokemonForm');
  document.getElementById('addFormTitle').textContent =
    `AÑADIR: ${species.name.toUpperCase()}`;

  // Rellenar habilidad con la primera disponible
  const abilities = species.abilities || [];
  document.getElementById('addAbility').value = abilities[0] || '';

  // Limpiar movimientos del learnset
  const learnset = species.learnset || [];
  ['move0','move1','move2','move3'].forEach((id, i) => {
    document.getElementById(id).value = learnset[i]?.id ?? '';
    document.getElementById(id).placeholder = learnset[i]
      ? `${learnset[i].id} (${learnset[i].name})`
      : `Slot ${i+1}`;
  });

  form.classList.remove('hidden');
  form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeAddForm() {
  document.getElementById('addPokemonForm').classList.add('hidden');
  State.selectedDexId = null;
}

async function onConfirmAddPokemon() {
  if (!State.selectedDexId) return;

  // Leer EVs
  const evs = {};
  STAT_KEYS.forEach(s => {
    evs[s] = parseInt(document.getElementById(`ev_${s}`).value) || 0;
  });

  // Leer movimientos (4 slots, null si vacío)
  const moves = ['move0','move1','move2','move3'].map(id => {
    const v = parseInt(document.getElementById(id).value);
    return isNaN(v) ? null : v;
  });

  const payload = {
    pokedex_id: State.selectedDexId,
    level:      parseInt(document.getElementById('addLevel').value) || 50,
    nature:     document.getElementById('addNature').value,
    ability:    document.getElementById('addAbility').value.trim() || null,
    moves,
    evs,
    ivs: Object.fromEntries(STAT_KEYS.map(s => [s, 31])), // IVs perfectos por defecto
  };

  const btn = document.getElementById('btnConfirmAdd');
  btn.disabled = true;
  btn.textContent = 'AÑADIENDO…';

  try {
    await apiFetch(`/team/${State.player.id}/add`, {
      method: 'POST',
      body:   JSON.stringify(payload),
    });
    showToast('Pokémon añadido al equipo ✓');
    closeAddForm();
    await loadMyTeam();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'CONFIRMAR';
  }
}

async function onRemovePokemon(slot) {
  try {
    await apiFetch(`/team/${State.player.id}/remove/${slot}`, { method: 'DELETE' });
    showToast(`Slot ${slot} liberado`);
    await loadMyTeam();
  } catch (err) {
    showToast(err.message, true);
  }
}

// ═══════════════════════════════════════════════════════════════
//  VISTA 3 — SETUP DE BATALLA
// ═══════════════════════════════════════════════════════════════

async function onCreateBattle() {
  const p2Id = parseInt(document.getElementById('inputP2Id').value);
  if (!p2Id || isNaN(p2Id)) {
    showToast('Ingresa el ID del rival', true);
    return;
  }
  if (p2Id === State.player.id) {
    showToast('No puedes batallar contra ti mismo', true);
    return;
  }

  const btn = document.getElementById('btnCreateBattle');
  btn.disabled = true;
  btn.textContent = 'CREANDO…';

  try {
    const data = await apiFetch('/battle/create', {
      method: 'POST',
      body:   JSON.stringify({ player1_id: State.player.id, player2_id: p2Id }),
    });
    State.battle = {
      id:          data.battle_id,
      player1_id:  data.player1_id,
      player2_id:  data.player2_id,
      turn_number: 1,
    };
    State.waitingForRival = false;
    State.selectedMoveId  = null;

    showToast('¡Batalla creada!');
    await refreshBattleState();
    showView('view-battle');
    logBattle(`▶ BATALLA INICIADA — Turno 1`, 'turn');
    logBattle(`${data.p1_lead} vs ${data.p2_lead}`, 'info');
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ CREAR BATALLA';
  }
}

// ═══════════════════════════════════════════════════════════════
//  VISTA 4 — BATALLA
// ═══════════════════════════════════════════════════════════════

/** Refresca el estado completo desde GET /api/battle/<id> */
async function refreshBattleState() {
  if (!State.battle?.id) return;
  try {
    const data = await apiFetch(`/battle/${State.battle.id}`);
    const b    = data.data;

    State.battle.turn_number  = b.turn_number;
    State.battle.battle_state = b.battle_state;
    State.battle.p1_active    = b.p1_active;
    State.battle.p2_active    = b.p2_active;
    State.battle.p1_team_hp   = b.p1_team_hp;
    State.battle.p2_team_hp   = b.p2_team_hp;
    State.battle.winner       = b.winner_player_id;

    renderBattleArena(b);

    if (b.battle_state === 'finished') {
      renderFinished(b.winner_player_id);
    }
  } catch (err) {
    showToast(`Refrescar: ${err.message}`, true);
  }
}

/** Dibuja la arena con los datos actuales del servidor */
function renderBattleArena(b) {
  // ── Identificar qué equipo es del jugador local ──────────
  const isP1    = State.player.id === b.player1_id;
  const myKey   = isP1 ? 'p1' : 'p2';
  const foeKey  = isP1 ? 'p2' : 'p1';

  const myActive  = b[`${myKey}_active`];
  const foeActive = b[`${foeKey}_active`];

  // ── Nombres e info ────────────────────────────────────────
  document.getElementById('p1Name').textContent  = myActive  || '???';
  document.getElementById('p2Name').textContent  = foeActive || '???';
  document.getElementById('panelPokeName').textContent = myActive || '???';

  // ── HP snapshots ──────────────────────────────────────────
  const myHp  = b[`${myKey}_team_hp`]  || {};
  const foeHp = b[`${foeKey}_team_hp`] || {};

  // Obtener HP máximo desde el State.team local
  const myPoke  = State.team.find(p => p.species_name === myActive);
  const myMaxHp = myPoke?.computed_stats?.hp ?? 1;
  updateHpBar('p1', myHp[myActive]  ?? myMaxHp, myMaxHp);

  // Para el rival usamos el valor actual como aprox del máximo (si no tenemos más info)
  const foeCurrentHp = foeHp[foeActive] ?? 0;
  // Guardamos el máximo la primera vez que lo vemos al 100%
  if (!State.battle._foeMaxHp) State.battle._foeMaxHp = {};
  if (!State.battle._foeMaxHp[foeActive] || foeCurrentHp > State.battle._foeMaxHp[foeActive]) {
    State.battle._foeMaxHp[foeActive] = foeCurrentHp;
  }
  const foeMaxHp = State.battle._foeMaxHp[foeActive] || 1;
  updateHpBar('p2', foeCurrentHp, foeMaxHp);

  // ── Sprites ───────────────────────────────────────────────
  const myPokeId  = myPoke?.pokedex_id;
  if (myPokeId) {
    document.getElementById('p1Sprite').src = getSpriteUrl(myPokeId);
    document.getElementById('p1Sprite').alt = myActive;
  }

  // ── Movimientos del Pokémon activo ────────────────────────
  renderMoveButtons(myPoke);

  // ── Turno y estado ────────────────────────────────────────
  document.getElementById('turnNumber').textContent = b.turn_number;
  document.getElementById('battleIdShort').textContent =
    b.id?.substring(0, 8) + '…';

  const statusBadge = document.getElementById('battleStatusBadge');
  statusBadge.textContent = b.battle_state === 'finished'
    ? 'TERMINADO' : State.waitingForRival ? 'ESPERANDO' : 'TU TURNO';
  statusBadge.style.background = b.battle_state === 'finished'
    ? 'var(--red-dim)' : State.waitingForRival ? 'rgba(255,225,53,.15)' : 'var(--green-dark)';
}

/** Renderiza los botones de movimientos del Pokémon activo */
function renderMoveButtons(pokemon) {
  const container = document.getElementById('movesButtons');
  container.innerHTML = '';
  if (!pokemon) return;

  const moves = pokemon.moves || [null, null, null, null];
  moves.forEach(moveId => {
    const btn = document.createElement('button');
    btn.className = 'move-btn';
    if (moveId === null) {
      btn.disabled  = true;
      btn.innerHTML = `<span>— VACÍO —</span>`;
    } else {
      btn.innerHTML = `
        <span>Move #${moveId}</span>
        <span class="move-type">ID: ${moveId}</span>
      `;
      btn.classList.toggle('selected', State.selectedMoveId === moveId);
      btn.disabled = State.waitingForRival;
      btn.addEventListener('click', () => selectMove(moveId));
    }
    container.appendChild(btn);
  });

  // Actualizar estado del botón de enviar
  document.getElementById('btnSendTurn').disabled =
    State.waitingForRival || State.selectedMoveId === null;
}

/** Selecciona un movimiento visualmente */
function selectMove(moveId) {
  State.selectedMoveId = moveId;
  document.querySelectorAll('.move-btn').forEach(btn => {
    const id = parseInt(btn.querySelector('span')?.textContent?.replace('Move #',''));
    btn.classList.toggle('selected', id === moveId);
  });
  document.getElementById('btnSendTurn').disabled = false;
}

// ── Enviar acción del turno ─────────────────────────────────────
async function onSendTurn() {
  if (!State.battle || State.selectedMoveId === null) return;

  const btn = document.getElementById('btnSendTurn');
  btn.disabled = true;
  btn.textContent = 'ENVIANDO…';

  try {
    const data = await apiFetch(`/battle/${State.battle.id}/turn`, {
      method: 'POST',
      body: JSON.stringify({
        player_id: State.player.id,
        action:    'attack',
        move_id:   State.selectedMoveId,
      }),
    });

    if (data.status === 'waiting') {
      // ── El rival aún no envió su acción ──────────────────
      State.waitingForRival = true;
      logBattle('⏳ Acción enviada. Esperando al rival…', 'waiting');
      renderMoveButtons(State.team.find(p =>
        p.species_name === State.battle.p1_active));
      document.getElementById('battleStatusBadge').textContent = 'ESPERANDO';
      return;
    }

    if (data.status === 'turn_resolved') {
      // ── Turno resuelto — procesar eventos ─────────────────
      State.waitingForRival = false;
      State.selectedMoveId  = null;
      processTurnEvents(data);
      await refreshBattleState();

      if (data.battle_over) {
        renderFinished(data.winner_player_id);
      }
    }
  } catch (err) {
    logBattle(`ERROR: ${err.message}`, 'error');
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = 'ENVIAR TURNO ▶';
  }
}

/** Procesa los eventos del turno y los escribe en el log */
function processTurnEvents(data) {
  logBattle(`── TURNO ${data.turn} ──`, 'turn');

  (data.events || []).forEach(ev => {
    if (ev.skipped) {
      logBattle(`${ev.reason === 'fainted_before_action' ? '💀 ' : ''}[${ev.actor.toUpperCase()}] acción saltada (${ev.reason})`, 'info');
      return;
    }
    if (ev.error) {
      logBattle(`⚠ ${ev.error}`, 'error');
      return;
    }
    if (ev.action === 'switch') {
      logBattle(`🔄 [${ev.actor.toUpperCase()}] ${ev.from} → ${ev.to}`, 'switch');
      return;
    }
    if (ev.damage !== undefined) {
      logBattle(
        `⚔ ${ev.attacker} usó Move#${ev.move_id} → ${ev.damage} daño a ${ev.target} (HP: ${ev.target_hp_after})`,
        'damage',
      );
      if (ev.fainted) {
        logBattle(`💀 ¡${ev.target} fue debilitado!`, 'fainted');
      }
    }
  });

  (data.post_events || []).forEach(ev => {
    if (ev.event === 'auto_switch') {
      logBattle(`↪ [${ev.player.toUpperCase()}] Sale ${ev.new_pokemon}`, 'switch');
    }
  });
}

/** Muestra el mensaje de fin de partida en el log */
function renderFinished(winnerId) {
  const isWinner = winnerId === State.player.id;
  logBattle(
    isWinner
      ? '🏆 ¡GANASTE LA BATALLA!'
      : winnerId
        ? `😔 Ganó el jugador ${winnerId}. ¡Mejor suerte la próxima!`
        : '⚖ ¡EMPATE SIMULTÁNEO!',
    'win',
  );
  document.getElementById('btnSendTurn').disabled    = true;
  document.getElementById('battleStatusBadge').textContent = 'TERMINADO';
}

// ── Botón refrescar ─────────────────────────────────────────────
async function onRefreshBattle() {
  const btn = document.getElementById('btnRefresh');
  btn.disabled = true;
  btn.textContent = '⟳ …';
  try {
    await refreshBattleState();
    showToast('Estado actualizado');
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ REFRESCAR';
  }
}
