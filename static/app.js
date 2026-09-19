(function () {
  const PIECE_THEME = "https://chessboardjs.com/img/chesspieces/wikipedia/{piece}.png";
  const STORE_KEY = "chessduel.session";
  const PROFILE_KEY = "chessduel.profile";
  const NAME = { w: "White", b: "Black" };
  const REASONS = {
    checkmate: "checkmate",
    stalemate: "stalemate",
    insufficient_material: "insufficient material",
    seventyfive_moves: "the 75 move rule",
    fivefold_repetition: "fivefold repetition",
    fifty_moves: "the 50 move rule",
    threefold_repetition: "threefold repetition",
    resignation: "resignation",
  };

  const $ = window.jQuery;
  const el = (id) => document.getElementById(id);
  const socket = io();
  const game = new Chess();

  let board = null;
  let session = null;
  let state = null;
  let pending = null;
  let toastTimer = null;

  function readProfile() {
    try {
      return JSON.parse(localStorage.getItem(PROFILE_KEY));
    } catch (e) {
      return null;
    }
  }

  function writeProfile(profile) {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(profile));
  }

  function getProfile() {
    const input = el("usernameInput");
    const stored = readProfile();
    const username = (input.value || (stored && stored.username) || "").trim();
    if (!username || username.length < 3 || username.length > 30) {
      el("lobbyError").textContent = "Enter a username between 3 and 30 characters";
      input.focus();
      return null;
    }
    input.value = username;
    return stored && stored.username === username && stored.auth_token
      ? stored
      : { username: username };
  }

  function readSession() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY));
    } catch (e) {
      return null;
    }
  }

  function writeSession() {
    localStorage.setItem(STORE_KEY, JSON.stringify(session));
  }

  function dropSession() {
    session = null;
    localStorage.removeItem(STORE_KEY);
  }

  function toast(message) {
    const t = el("toast");
    t.textContent = message;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2600);
  }

  function showLobby(message) {
    const profile = readProfile();
    if (profile) el("usernameInput").value = profile.username;
    el("game").hidden = true;
    el("lobby").hidden = false;
    el("lobbyError").textContent = message || "";
    if (board) {
      board.destroy();
      board = null;
    }
    state = null;
    pending = null;
    el("promo").hidden = true;
    history.replaceState(null, "", location.pathname);
  }

  function showGame() {
    el("lobby").hidden = true;
    el("game").hidden = false;
    el("roomCode").textContent = session.code;
    if (!board) {
      board = Chessboard("board", {
        position: "start",
        orientation: session.color === "w" ? "white" : "black",
        draggable: true,
        pieceTheme: PIECE_THEME,
        onDragStart: onDragStart,
        onDrop: onDrop,
        onSnapEnd: onSnapEnd,
      });
    }
  }

  function canMove(piece) {
    if (!state || state.result) return false;
    if (!state.joined.w || !state.joined.b) return false;
    if (game.turn() !== session.color) return false;
    return Boolean(piece) && piece.charAt(0) === session.color;
  }

  function onDragStart(source, piece) {
    return canMove(piece);
  }

  function onDrop(source, target) {
    if (source === target || target === "offboard") return "snapback";
    const candidates = game.moves({ verbose: true }).filter((m) => m.from === source && m.to === target);
    if (!candidates.length) return "snapback";
    if (candidates[0].promotion) {
      pending = { from: source, to: target };
      openPromo();
      return "snapback";
    }
    game.move({ from: source, to: target });
    sendMove(source, target);
  }

  function onSnapEnd() {
    board.position(game.fen());
  }

  function sendMove(from, to, promotion) {
    socket.emit("move", { code: session.code, token: session.token, from: from, to: to, promotion: promotion });
  }

  function openPromo() {
    const box = el("promoChoices");
    box.innerHTML = "";
    ["q", "r", "b", "n"].forEach((p) => {
      const button = document.createElement("button");
      const img = document.createElement("img");
      img.src = PIECE_THEME.replace("{piece}", session.color + p.toUpperCase());
      img.alt = p;
      button.appendChild(img);
      button.addEventListener("click", () => choosePromo(p));
      box.appendChild(button);
    });
    el("promo").hidden = false;
  }

  function choosePromo(p) {
    el("promo").hidden = true;
    if (!pending) return;
    const move = game.move({ from: pending.from, to: pending.to, promotion: p });
    if (move) {
      board.position(game.fen());
      sendMove(pending.from, pending.to, p);
    }
    pending = null;
  }

  function kingSquare(color) {
    const rows = game.board();
    for (let r = 0; r < 8; r++) {
      for (let c = 0; c < 8; c++) {
        const p = rows[r][c];
        if (p && p.type === "k" && p.color === color) return "abcdefgh".charAt(c) + (8 - r);
      }
    }
    return null;
  }

  function paintHighlights() {
    $("#board .square-55d63").removeClass("hl-move hl-check");
    if (state.last_move) {
      $("#board .square-" + state.last_move.from).addClass("hl-move");
      $("#board .square-" + state.last_move.to).addClass("hl-move");
    }
    if (state.in_check) {
      const sq = kingSquare(state.turn);
      if (sq) $("#board .square-" + sq).addClass("hl-check");
    }
  }

  function renderMoves() {
    const list = el("moves");
    list.innerHTML = "";
    for (let i = 0; i < state.history.length; i += 2) {
      const row = document.createElement("div");
      row.className = "row";
      const n = document.createElement("span");
      n.className = "n";
      n.textContent = i / 2 + 1 + ".";
      const w = document.createElement("span");
      w.textContent = state.history[i];
      const b = document.createElement("span");
      b.textContent = state.history[i + 1] || "";
      row.append(n, w, b);
      list.appendChild(row);
    }
    list.scrollTop = list.scrollHeight;
  }

  function renderStatus() {
    const me = session.color;
    const opp = me === "w" ? "b" : "w";
    const bothJoined = state.joined.w && state.joined.b;
    let text;
    if (state.result) {
      const why = REASONS[state.result.reason] || state.result.reason;
      if (!state.result.winner) text = "Draw by " + why;
      else text = (state.result.winner === me ? "You win" : "You lose") + " by " + why;
    } else if (!bothJoined) {
      text = "Waiting for opponent. Share the room code.";
    } else {
      const other = state.vs_ai ? "Computer's turn" : "Opponent's turn";
      text = (state.turn === me ? "Your turn" : other) + (state.in_check ? " (check)" : "");
    }
    const status = el("status");
    status.textContent = text;
    status.className = state.result ? "over" : bothJoined && state.turn === me ? "mine" : "";

    const oppName = state.vs_ai ? "Computer" : "Opponent";
    document.querySelector(".room").hidden = Boolean(state.vs_ai);
    const youName = state.usernames[me] || "You";
    const opponentName = state.vs_ai ? "Computer" : (state.usernames[opp] || "Opponent");
    el("youLabel").textContent = youName + " (" + NAME[me] + ")";
    el("opponentLabel").textContent = state.joined[opp]
      ? opponentName + " (" + NAME[opp] + ")" + (state.online[opp] ? "" : " disconnected")
      : "Waiting for opponent";
    el("youBar").dataset.online = "true";
    el("opponentBar").dataset.online = String(state.online[opp]);
    el("youBar").classList.toggle("active", bothJoined && !state.result && state.turn === me);
    el("opponentBar").classList.toggle("active", bothJoined && !state.result && state.turn === opp);
    el("resignBtn").disabled = Boolean(state.result) || !bothJoined;
    el("leaveBtn").disabled = bothJoined && !state.result;
  }

  function applyState(s) {
    state = s;
    game.load(s.fen);
    if (board) board.position(s.fen);
    paintHighlights();
    renderMoves();
    renderStatus();
  }

  function joinFromInput() {
    const code = el("codeInput").value.trim().toUpperCase();
    if (code.length !== 5) {
      el("lobbyError").textContent = "Enter the 5 character room code";
      return;
    }
    el("lobbyError").textContent = "";
    const profile = getProfile();
    if (!profile) return;
    writeProfile(profile);
    const stored = readSession();
    const data = stored && stored.code === code
      ? { code: code, token: stored.token, user_id: profile.user_id, username: profile.username, auth_token: profile.auth_token }
      : { code: code, user_id: profile.user_id, username: profile.username, auth_token: profile.auth_token };
    socket.emit("join_game", data);
  }

  socket.on("connect", () => {
    const room = (new URLSearchParams(location.search).get("room") || "").trim().toUpperCase();
    session = readSession();
    const profile = readProfile();
    if (profile) el("usernameInput").value = profile.username;
    if (session && profile && (!room || room === session.code)) {
      socket.emit("join_game", { code: session.code, token: session.token, user_id: profile.user_id, username: profile.username, auth_token: profile.auth_token });
    } else if (room && profile) {
      socket.emit("join_game", { code: room, user_id: profile.user_id, username: profile.username, auth_token: profile.auth_token });
    }
  });

  socket.on("joined", (d) => {
    session = { code: d.code, token: d.token, color: d.color, user_id: d.user_id, username: d.username, auth_token: d.auth_token };
    writeSession();
    writeProfile({ user_id: d.user_id, username: d.username, auth_token: d.auth_token });
    history.replaceState(null, "", "?room=" + d.code);
    showGame();
    applyState(d.state);
  });

  socket.on("state", (s) => {
    if (session && board) applyState(s);
  });

  socket.on("error_message", (e) => {
    if (e.fatal) {
      dropSession();
      showLobby(e.message);
    } else {
      toast(e.message);
    }
  });

  el("createBtn").addEventListener("click", () => {
    const profile = getProfile();
    if (!profile) return;
    writeProfile(profile);
    socket.emit("create_game", profile);
  });
  el("aiBtn").addEventListener("click", () => {
    const profile = getProfile();
    if (!profile) return;
    writeProfile(profile);
    socket.emit("create_game", { ...profile, mode: "ai" });
  });
  async function loadHistory() {
    const profile = readProfile();
    if (!profile || !profile.user_id) {
      el("lobbyError").textContent = "Create or join a game first to view history";
      return;
    }
    const list = el("historyList");
    list.innerHTML = "<p class=\"history-empty\">Loading...</p>";
    el("historyModal").hidden = false;
    try {
      const response = await fetch("/api/history/" + encodeURIComponent(profile.user_id), {
        headers: { Authorization: "Bearer " + profile.auth_token }
      });
      if (!response.ok) throw new Error("history request failed");
      const data = await response.json();
      if (!data.games.length) {
        list.innerHTML = "<p class=\"history-empty\">No completed games yet.</p>";
        return;
      }
      list.innerHTML = "";
      data.games.forEach((item) => {
        const row = document.createElement("div");
        row.className = "history-row";
        const date = item.ended_at ? new Date(item.ended_at).toLocaleString() : "";
        const result = item.outcome.charAt(0).toUpperCase() + item.outcome.slice(1);
        const reason = REASONS[item.reason] || item.reason || "completed";
        row.innerHTML = "<div><strong>" + escapeHtml(item.opponent) + "</strong><span>" + result + " · " + escapeHtml(reason) + "</span></div><div><strong>" + item.move_count + " moves</strong><span>" + escapeHtml(date) + "</span></div>";
        list.appendChild(row);
      });
    } catch (error) {
      list.innerHTML = "<p class=\"history-empty\">Unable to load history.</p>";
    }
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;"}[char]));
  }

  el("historyBtn").addEventListener("click", loadHistory);
  el("historyClose").addEventListener("click", () => { el("historyModal").hidden = true; });
  el("historyModal").addEventListener("click", (e) => { if (e.target === el("historyModal")) el("historyModal").hidden = true; });

  el("joinBtn").addEventListener("click", joinFromInput);
  el("codeInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") joinFromInput();
  });
  el("codeInput").addEventListener("input", (e) => {
    e.target.value = e.target.value.toUpperCase();
  });

  el("copyBtn").addEventListener("click", () => {
    if (!session) return;
    navigator.clipboard.writeText(session.code).then(
      () => toast("Room code copied"),
      () => toast(session.code)
    );
  });

  el("resignBtn").addEventListener("click", () => {
    if (window.confirm("Resign this game?")) {
      socket.emit("resign", { code: session.code, token: session.token });
    }
  });

  el("leaveBtn").addEventListener("click", () => {
    if (!session) return;
    if (state && state.joined.w && state.joined.b && !state.result) {
      toast("Resign before leaving the match");
      return;
    }
    socket.emit("leave_game", { code: session.code, token: session.token });
    dropSession();
    showLobby("");
  });

  el("promo").addEventListener("click", (e) => {
    if (e.target === el("promo")) {
      el("promo").hidden = true;
      pending = null;
    }
  });

  window.addEventListener("resize", () => {
    if (board) board.resize();
  });
})();