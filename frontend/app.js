// ═══════════════════════════════════════════════════════════════
//  POKÉMON ARENA — app.js  (versión completa con todos los fixes)
//  SPA que consume el API Gateway en /api/...
// ═══════════════════════════════════════════════════════════════

const API_BASE = '/api';
// Si abres index.html directo sin nginx cambia a:
// const API_BASE = 'http://localhost:5000/api';

// ─── Naturalezas (Gen 3-9) ──────────────────────────────────────
const NATURES = [
  "Hardy","Lonely","Brave","Adamant","Naughty",
  "Bold","Docile","Relaxed","Impish","Lax",
  "Timid","Hasty","Serious","Jolly","Naive",
  "Modest","Mild","Quiet","Bashful","Rash",
  "Calm","Gentle","Sassy","Careful","Quirky",
];

const STAT_KEYS  = ["hp","attack","defense","sp_attack","sp_defense","speed"];
const STAT_LABEL = {
  hp:"HP", attack:"ATK", defense:"DEF",
  sp_attack:"SPA", sp_defense:"SPD", speed:"SPE"
};

// ─── Estado global ──────────────────────────────────────────────
const State = {
  player:          null,   // { id, name }
  pokedex:         [],     // lista de especies
  team:            [],     // instancias del equipo del jugador
  selectedDexId:   null,   // pokedex_id seleccionado para añadir
  battle:          null,   // objeto batalla activa
  selectedMoveId:  null,   // move_id elegido para el turno
  waitingForRival: false,  // true si ya enviamos acción este turno
};

// ═══════════════════════════════════════════════════════════════
//  UTILIDADES
// ═══════════════════════════════════════════════════════════════

function showToast(msg, isError = false) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.toggle('toast--error', isError);
  t.classList.add('visible');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('visible'), 2800);
}

function logBattle(msg, type = 'info') {
  const log  = document.getElementById('battleLog');
  const line = document.createElement('p');
  line.className = `log-entry log-entry--${type}`;
  line.textContent = msg;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function showView(id) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  window.scrollTo(0, 0);
}

async function apiFetch(path, options = {}) {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
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

function hpPercent(current, max) {
  return Math.max(0, Math.min(100, Math.round((current / max) * 100)));
}

function updateHpBar(prefix, currentHp, maxHp) {
  const bar  = document.getElementById(`${prefix}HpBar`);
  const text = document.getElementById(`${prefix}HpText`);
  if (!bar || !text) return;
  const pct = hpPercent(currentHp, maxHp);
  bar.style.width = `${pct}%`;
  bar.classList.remove('hp--medium', 'hp--low');
  if (pct <= 20)      bar.classList.add('hp--low');
  else if (pct <= 50) bar.classList.add('hp--medium');
  text.textContent = `${currentHp} / ${maxHp}`;
}

function getSpriteUrl(pokedexId) {
  return `https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/${pokedexId}.png`;
}

// ═══════════════════════════════════════════════════════════════
//  INICIALIZACIÓN
// ═══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  populateNatureSelect();
  buildEvInputs();
  checkServicesHealth();

  // ── Vista 1: Registro ──────────────────────────────────────
  document.getElementById('formRegister')
    .addEventListener('submit', onRegister);

  document.getElementById('btnLoadPokedex')
    .addEventListener('click', () => {
      showView('view-team');
      loadPokedex();
      loadMyTeam();
    });

  // ── Vista 2: Equipo ────────────────────────────────────────
  document.getElementById('btnConfirmAdd')
    .addEventListener('click', onConfirmAddPokemon);

  document.getElementById('btnCancelAdd')
    .addEventListener('click', closeAddForm);

  document.getElementById('btnGoToBattle')
    .addEventListener('click', () => {
      document.getElementById('setupP1Name').textContent = State.player.name;
      document.getElementById('setupP1Id').textContent   = `ID: ${State.player.id}`;
      // Ocultar el share box por si quedó visible de una batalla anterior
      document.getElementById('shareBattleId').classList.add('hidden');
      showView('view-setup');
    });

  // ── Vista 3: Setup de batalla ──────────────────────────────
  document.getElementById('btnCreateBattle')
    .addEventListener('click', onCreateBattle);

  document.getElementById('btnJoinBattle')
    .addEventListener('click', onJoinBattle);

  document.getElementById('btnBackToTeam')
    .addEventListener('click', () => showView('view-team'));

  // Copiar UUID al portapapeles
  document.getElementById('btnCopyBattleId')
    .addEventListener('click', () => {
      const id = document.getElementById('battleIdCreated').textContent;
      navigator.clipboard.writeText(id)
        .then(() => showToast('UUID copiado al portapapeles ✓'))
        .catch(() => {
          // Fallback manual si clipboard API no está disponible
          const el = document.getElementById('battleIdCreated');
          const range = document.createRange();
          range.selectNode(el);
          window.getSelection().removeAllRanges();
          window.getSelection().addRange(range);
          showToast('Seleccionado — usa Ctrl+C para copiar', false);
        });
    });

  // Botón "IR A LA BATALLA" que aparece después de crear
  document.getElementById('btnGoToBattleAfterCreate')
    .addEventListener('click', async () => {
      await refreshBattleState();
      showView('view-battle');
      logBattle(`▶ BATALLA INICIADA — Turno 1`, 'turn');
      if (State.battle) {
        logBattle(`${State.battle.p1_active || '???'} vs ${State.battle.p2_active || '???'}`, 'info');
      }
    });

  // ── Vista 4: Batalla ───────────────────────────────────────
  document.getElementById('btnSendTurn')
    .addEventListener('click', onSendTurn);

  document.getElementById('btnRefresh')
    .addEventListener('click', onRefreshBattle);
});

function populateNatureSelect() {
  const sel = document.getElementById('addNature');
  NATURES.forEach(n => {
    const opt = document.createElement('option');
    opt.value = n;
    opt.textContent = n;
    sel.appendChild(opt);
  });
}

function buildEvInputs() {
  const container = document.getElementById('evInputs');
  STAT_KEYS.forEach(stat => {
    const wrap = document.createElement('label');
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
    showToast(`¡Bienvenido, ${State.player.name}! Tu ID es: ${State.player.id}`);
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
      <img src="${sp.sprite_url || getSpriteUrl(sp.id)}" alt="${sp.name}"
           onerror="this.src=''" />
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
    const li = document.createElement('li');
    li.className = 'team-item';
    const hp  = pk.computed_stats?.hp  ?? '?';
    const spe = pk.computed_stats?.speed ?? '?';
    const spriteUrl = pk.sprite_url || getSpriteUrl(pk.pokedex_id);
    li.innerHTML = `
      <span class="team-item__slot">${pk.slot}</span>
      <img src="${spriteUrl}" alt="${pk.species_name}" onerror="this.src=''" />
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

function openAddForm(species) {
  State.selectedDexId = species.id;
  document.getElementById('addFormTitle').textContent =
    `AÑADIR: ${species.name.toUpperCase()}`;

  const abilities = species.abilities || [];
  document.getElementById('addAbility').value = abilities[0] || '';

  const learnset = species.learnset || [];
  ['move0','move1','move2','move3'].forEach((id, i) => {
    document.getElementById(id).value = learnset[i]?.id ?? '';
    document.getElementById(id).placeholder = learnset[i]
      ? `${learnset[i].id} (${learnset[i].name})`
      : `Slot ${i+1}`;
  });

  const form = document.getElementById('addPokemonForm');
  form.classList.remove('hidden');
  form.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function closeAddForm() {
  document.getElementById('addPokemonForm').classList.add('hidden');
  State.selectedDexId = null;
}

async function onConfirmAddPokemon() {
  if (!State.selectedDexId) return;

  const evs = {};
  STAT_KEYS.forEach(s => {
    evs[s] = parseInt(document.getElementById(`ev_${s}`).value) || 0;
  });

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
    ivs: Object.fromEntries(STAT_KEYS.map(s => [s, 31])),
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
    showToast('Ingresa el ID numérico del rival', true);
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
      body: JSON.stringify({
        player1_id: State.player.id,
        player2_id: p2Id,
      }),
    });

    // Guardar estado de batalla
    State.battle = {
      id:          data.battle_id,
      player1_id:  data.player1_id,
      player2_id:  data.player2_id,
      turn_number: 1,
      _foeMaxHp:   {},
    };
    State.waitingForRival = false;
    State.selectedMoveId  = null;

    // ── Mostrar UUID ANTES de ir a la batalla ─────────────────
    // El jugador debe compartirlo con el rival antes de continuar
    document.getElementById('battleIdCreated').textContent = data.battle_id;
    document.getElementById('shareBattleId').classList.remove('hidden');
    document.getElementById('shareBattleId').scrollIntoView({ behavior: 'smooth' });
    showToast('¡Batalla creada! Comparte el UUID con tu rival.');

    // El botón "IR A LA BATALLA" lleva a la arena (ver listener en DOMContentLoaded)

  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ CREAR BATALLA';
  }
}

async function onJoinBattle() {
  const battleId = document.getElementById('inputJoinBattleId').value.trim();
  if (!battleId) {
    showToast('Pega el UUID de batalla que te dio el rival', true);
    return;
  }

  const btn = document.getElementById('btnJoinBattle');
  btn.disabled = true;
  btn.textContent = 'UNIÉNDOSE…';

  try {
    const data = await apiFetch(`/battle/${battleId}`);
    const b    = data.data;

    // Verificar que este jugador participa en la batalla
    if (b.player1_id !== State.player.id && b.player2_id !== State.player.id) {
      showToast(
        `Tu jugador (ID ${State.player.id}) no participa en esta batalla. ` +
        `Jugadores: ${b.player1_id} vs ${b.player2_id}`,
        true
      );
      return;
    }

    if (b.battle_state === 'finished') {
      showToast('Esta batalla ya terminó.', true);
      return;
    }

    State.battle = {
      id:          b.id,
      player1_id:  b.player1_id,
      player2_id:  b.player2_id,
      turn_number: b.turn_number,
      _foeMaxHp:   {},
    };
    State.waitingForRival = false;
    State.selectedMoveId  = null;

    showToast('¡Te uniste a la batalla!');
    await refreshBattleState();
    showView('view-battle');
    logBattle(`▶ UNIÉNDOSE A BATALLA — Turno ${b.turn_number}`, 'turn');
    logBattle(`Eres el jugador ${State.player.id === b.player1_id ? '1' : '2'}`, 'info');

  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ UNIRSE A BATALLA';
  }
}

// ═══════════════════════════════════════════════════════════════
//  VISTA 4 — BATALLA
// ═══════════════════════════════════════════════════════════════

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
    showToast(`Error al refrescar: ${err.message}`, true);
  }
}

function renderBattleArena(b) {
  // Determinar qué jugador somos (p1 o p2)
  const isP1   = State.player.id === b.player1_id;
  const myKey  = isP1 ? 'p1' : 'p2';
  const foeKey = isP1 ? 'p2' : 'p1';

  const myActive  = b[`${myKey}_active`];
  const foeActive = b[`${foeKey}_active`];

  // Nombres
  document.getElementById('p1Name').textContent = myActive  || '???';
  document.getElementById('p2Name').textContent = foeActive || '???';
  document.getElementById('panelPokeName').textContent = myActive || '???';

  // HP
  const myHpMap  = b[`${myKey}_team_hp`]  || {};
  const foeHpMap = b[`${foeKey}_team_hp`] || {};

  // HP propio — usamos el máximo del State.team local
  const myPoke  = State.team.find(p => p.species_name === myActive);
  const myMaxHp = myPoke?.computed_stats?.hp ?? 1;
  const myCurHp = myHpMap[myActive] ?? myMaxHp;
  updateHpBar('p1', myCurHp, myMaxHp);

  // HP rival — guardamos el máximo la primera vez que lo vemos completo
  const foeCurHp = foeHpMap[foeActive] ?? 0;
  if (!State.battle._foeMaxHp) State.battle._foeMaxHp = {};
  if (
    !State.battle._foeMaxHp[foeActive] ||
    foeCurHp > State.battle._foeMaxHp[foeActive]
  ) {
    State.battle._foeMaxHp[foeActive] = foeCurHp;
  }
  const foeMaxHp = State.battle._foeMaxHp[foeActive] || 1;
  updateHpBar('p2', foeCurHp, foeMaxHp);

  // Sprite del jugador
  if (myPoke?.pokedex_id) {
    const sprite = document.getElementById('p1Sprite');
    sprite.src = getSpriteUrl(myPoke.pokedex_id);
    sprite.alt = myActive;
  }

  // Movimientos disponibles
  renderMoveButtons(myPoke);

  // Meta info
  document.getElementById('turnNumber').textContent = b.turn_number;
  document.getElementById('battleIdShort').textContent =
    (b.id || '').substring(0, 8) + '…';

  const badge = document.getElementById('battleStatusBadge');
  if (b.battle_state === 'finished') {
    badge.textContent = 'TERMINADO';
    badge.style.background = 'var(--red-dim)';
    badge.style.color = 'var(--red)';
  } else if (State.waitingForRival) {
    badge.textContent = 'ESPERANDO';
    badge.style.background = 'rgba(255,225,53,.15)';
    badge.style.color = 'var(--yellow)';
  } else {
    badge.textContent = 'TU TURNO';
    badge.style.background = 'var(--green-dark)';
    badge.style.color = 'var(--green)';
  }
}

function renderMoveButtons(pokemon) {
  const container = document.getElementById('movesButtons');
  container.innerHTML = '';
  if (!pokemon) return;

  const moves = pokemon.moves || [null, null, null, null];
  moves.forEach(moveId => {
    const btn = document.createElement('button');
    btn.className = 'move-btn';

    if (moveId === null) {
      btn.disabled = true;
      btn.innerHTML = `<span>— VACÍO —</span>`;
    } else {
      btn.innerHTML = `
        <span>Move #${moveId}</span>
        <span class="move-type">ID: ${moveId}</span>
      `;
      btn.classList.toggle('selected', State.selectedMoveId === moveId);
      btn.disabled = State.waitingForRival ||
                     State.battle?.battle_state === 'finished';
      btn.addEventListener('click', () => selectMove(moveId));
    }
    container.appendChild(btn);
  });

  document.getElementById('btnSendTurn').disabled =
    State.waitingForRival ||
    State.selectedMoveId === null ||
    State.battle?.battle_state === 'finished';
}

function selectMove(moveId) {
  State.selectedMoveId = moveId;
  document.querySelectorAll('.move-btn').forEach(btn => {
    const spanText = btn.querySelector('span')?.textContent || '';
    const id = parseInt(spanText.replace('Move #', ''));
    btn.classList.toggle('selected', id === moveId);
  });
  document.getElementById('btnSendTurn').disabled = false;
}

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
      // Acción registrada, rival aún no envió la suya
      State.waitingForRival = true;
      logBattle('⏳ Acción enviada. Esperando al rival…', 'waiting');
      // Re-renderizar botones desactivados
      const myActive = State.battle.p1_active || State.battle.p2_active;
      const myPoke   = State.team.find(p => p.species_name === myActive);
      renderMoveButtons(myPoke);
      document.getElementById('battleStatusBadge').textContent = 'ESPERANDO';
      return;
    }

    if (data.status === 'turn_resolved') {
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

function processTurnEvents(data) {
  logBattle(`── TURNO ${data.turn} ──`, 'turn');

  (data.events || []).forEach(ev => {
    if (ev.skipped) {
      logBattle(
        `[${(ev.actor || '?').toUpperCase()}] acción saltada (${ev.reason || 'debilitado'})`,
        'info'
      );
      return;
    }
    if (ev.error) {
      logBattle(`⚠ ${ev.error}`, 'error');
      return;
    }
    if (ev.action === 'switch') {
      logBattle(`🔄 [${(ev.actor || '?').toUpperCase()}] ${ev.from} → ${ev.to}`, 'switch');
      return;
    }
    if (ev.damage !== undefined) {
      logBattle(
        `⚔ ${ev.attacker} usó Move#${ev.move_id} → ${ev.damage} daño a ${ev.target} (HP: ${ev.target_hp_after})`,
        'damage'
      );
      if (ev.fainted) {
        logBattle(`💀 ¡${ev.target} fue debilitado!`, 'fainted');
      }
    }
  });

  (data.post_events || []).forEach(ev => {
    if (ev.event === 'auto_switch') {
      logBattle(`↪ [${(ev.player || '?').toUpperCase()}] Sale ${ev.new_pokemon}`, 'switch');
    }
  });
}

function renderFinished(winnerId) {
  const isWinner = winnerId === State.player.id;
  logBattle(
    isWinner
      ? '🏆 ¡GANASTE LA BATALLA!'
      : winnerId
        ? `😔 Ganó el jugador ${winnerId}. ¡Mejor suerte la próxima!`
        : '⚖ ¡EMPATE SIMULTÁNEO!',
    'win'
  );
  document.getElementById('btnSendTurn').disabled = true;
  document.querySelectorAll('.move-btn').forEach(b => b.disabled = true);
  const badge = document.getElementById('battleStatusBadge');
  badge.textContent = 'TERMINADO';
  badge.style.background = 'var(--red-dim)';
  badge.style.color = 'var(--red)';
}

async function onRefreshBattle() {
  const btn = document.getElementById('btnRefresh');
  btn.disabled = true;
  btn.textContent = '⟳ …';

  // Si estábamos esperando al rival, comprobamos si ya resolvió el turno
  const wasWaiting = State.waitingForRival;

  try {
    await refreshBattleState();

    // Si el turno avanzó mientras esperábamos, desbloqueamos los botones
    if (wasWaiting && State.battle.turn_number > (State.battle._lastKnownTurn || 0)) {
      State.waitingForRival = false;
      State.selectedMoveId  = null;
      State.battle._lastKnownTurn = State.battle.turn_number;
      logBattle(`▶ Turno ${State.battle.turn_number} comenzó — es tu turno`, 'turn');
      showToast('¡El rival jugó! Es tu turno.');
    } else if (!wasWaiting) {
      showToast('Estado actualizado');
    }

    State.battle._lastKnownTurn = State.battle.turn_number;
  } finally {
    btn.disabled = false;
    btn.textContent = '⟳ REFRESCAR';
  }
}
