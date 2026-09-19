import os
import sys

import pytest
import chess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, db, games, socketio, User, UserCredential, GameRecord, GameMove


@pytest.fixture(autouse=True)
def reset_games():
    games.clear()
    with app.app_context():
        db.session.query(GameMove).delete()
        db.session.query(GameRecord).delete()
        db.session.query(UserCredential).delete()
        db.session.query(User).delete()
        db.session.commit()
    yield
    games.clear()


def events(client, name):
    return [m["args"][0] for m in client.get_received() if m["name"] == name]


def make_pair():
    a = socketio.test_client(app)
    b = socketio.test_client(app)
    a.emit("create_game", {"username": "PlayerOne"})
    ja = events(a, "joined")[0]
    b.emit("join_game", {"code": ja["code"], "username": "PlayerTwo"})
    jb = events(b, "joined")[0]
    a.get_received()
    return a, b, ja, jb


def move(client, joined, src, dst, promotion=None):
    client.emit(
        "move",
        {"code": joined["code"], "token": joined["token"], "from": src, "to": dst, "promotion": promotion},
    )


def test_create_creates_persistent_user():
    c = socketio.test_client(app)
    c.emit("create_game", {"username": "Chiranjeev"})
    joined = events(c, "joined")[0]
    with app.app_context():
        user = db.session.get(User, joined["user_id"])
        assert user is not None
        assert user.username == "Chiranjeev"


def test_duplicate_username_rejected():
    a = socketio.test_client(app)
    b = socketio.test_client(app)
    a.emit("create_game", {"username": "Chiranjeev"})
    b.emit("create_game", {"username": "chiranjeev"})
    error = events(b, "error_message")[0]
    assert error["fatal"] is True
    assert error["message"] == "Enter a username between 3 and 30 characters"


def test_existing_user_can_reconnect_with_user_id():
    a = socketio.test_client(app)
    a.emit("create_game", {"username": "Chiranjeev"})
    joined = events(a, "joined")[0]
    a.disconnect()
    b = socketio.test_client(app)
    b.emit("create_game", {"user_id": joined["user_id"], "username": joined["username"], "auth_token": joined["auth_token"]})
    joined_again = events(b, "joined")[0]
    assert joined_again["user_id"] == joined["user_id"]
    with app.app_context():
        assert User.query.count() == 1


def test_create_and_join_assign_colors():
    a, b, ja, jb = make_pair()
    assert ja["color"] == "w"
    assert jb["color"] == "b"
    assert jb["state"]["joined"] == {"w": True, "b": True}
    assert len(ja["code"]) == 5


def test_join_unknown_code():
    c = socketio.test_client(app)
    c.emit("join_game", {"code": "ZZZZZ", "username": "PlayerThree"})
    err = events(c, "error_message")[0]
    assert err["message"] == "Game not found"
    assert err["fatal"] is True


def test_third_player_rejected():
    a, b, ja, jb = make_pair()
    c = socketio.test_client(app)
    c.emit("join_game", {"code": ja["code"], "username": "PlayerThree"})
    assert events(c, "error_message")[0]["message"] == "Game is full"


def test_move_broadcasts_to_both_players():
    a, b, ja, jb = make_pair()
    move(a, ja, "e2", "e4")
    sa = events(a, "state")[-1]
    sb = events(b, "state")[-1]
    assert sa["history"] == ["e4"]
    assert sb["fen"] == sa["fen"]
    assert sb["turn"] == "b"
    assert sb["last_move"] == {"from": "e2", "to": "e4"}


def test_wrong_turn_rejected():
    a, b, ja, jb = make_pair()
    move(b, jb, "e7", "e5")
    assert events(b, "error_message")[0]["message"] == "Not your turn"
    assert games[ja["code"]].san == []


def test_illegal_move_rejected():
    a, b, ja, jb = make_pair()
    move(a, ja, "e2", "e5")
    assert events(a, "error_message")[0]["message"] == "Illegal move"
    assert games[ja["code"]].san == []


def test_malformed_move_rejected():
    a, b, ja, jb = make_pair()
    a.emit("move", {"code": ja["code"], "token": ja["token"], "from": None, "to": 5})
    assert events(a, "error_message")[0]["message"] == "Invalid move"


def test_move_with_wrong_token_rejected():
    a, b, ja, jb = make_pair()
    a.emit("move", {"code": ja["code"], "token": "bad", "from": "e2", "to": "e4"})
    assert events(a, "error_message")[0]["fatal"] is True
    assert games[ja["code"]].san == []


def test_cannot_move_before_opponent_joins():
    a = socketio.test_client(app)
    a.emit("create_game", {"username": "PlayerOne"})
    ja = events(a, "joined")[0]
    move(a, ja, "e2", "e4")
    assert events(a, "error_message")[0]["message"] == "Waiting for opponent"


def test_fools_mate_ends_game():
    a, b, ja, jb = make_pair()
    move(a, ja, "f2", "f3")
    move(b, jb, "e7", "e5")
    move(a, ja, "g2", "g4")
    move(b, jb, "d8", "h4")
    final = events(a, "state")[-1]
    assert final["result"] == {"winner": "b", "reason": "checkmate"}
    assert final["in_check"] is True
    move(a, ja, "a2", "a3")
    assert events(a, "error_message")[0]["message"] == "Game is over"


def test_promotion_requires_valid_piece():
    a, b, ja, jb = make_pair()
    move(a, ja, "e2", "e4", promotion="x")
    assert events(a, "error_message")[0]["message"] == "Invalid promotion"


def test_rejoin_with_token_keeps_seat():
    a, b, ja, jb = make_pair()
    move(a, ja, "e2", "e4")
    a.disconnect()
    assert events(b, "state")[-1]["online"]["w"] is False
    a2 = socketio.test_client(app)
    a2.emit("join_game", {"code": ja["code"], "token": ja["token"], "user_id": ja["user_id"], "username": ja["username"], "auth_token": ja["auth_token"]})
    rejoined = events(a2, "joined")[0]
    assert rejoined["color"] == "w"
    assert rejoined["state"]["history"] == ["e4"]
    assert events(b, "state")[-1]["online"]["w"] is True


def test_resign_ends_game():
    a, b, ja, jb = make_pair()
    a.emit("resign", {"code": ja["code"], "token": ja["token"]})
    final = events(b, "state")[-1]
    assert final["result"] == {"winner": "b", "reason": "resignation"}


def test_games_are_isolated():
    a, b, ja, jb = make_pair()
    c, d, jc, jd = make_pair()
    move(a, ja, "e2", "e4")
    assert games[ja["code"]].san == ["e4"]
    assert games[jc["code"]].san == []


def make_ai():
    a = socketio.test_client(app)
    a.emit("create_game", {"username": "PlayerOne", "mode": "ai"})
    return a, events(a, "joined")[0]


def test_ai_game_starts_with_both_seats_filled():
    a, ja = make_ai()
    assert ja["color"] == "w"
    assert ja["state"]["vs_ai"] is True
    assert ja["state"]["joined"] == {"w": True, "b": True}
    assert ja["state"]["online"] == {"w": True, "b": True}


def test_ai_replies_after_human_move():
    a, ja = make_ai()
    move(a, ja, "e2", "e4")
    states = events(a, "state")
    assert len(states) == 2
    assert states[0]["turn"] == "b"
    assert states[1]["turn"] == "w"
    assert len(states[1]["history"]) == 2


def test_ai_seat_cannot_be_taken():
    a, ja = make_ai()
    b = socketio.test_client(app)
    b.emit("join_game", {"code": ja["code"], "username": "PlayerTwo"})
    assert events(b, "error_message")[0]["message"] == "Game is full"


def test_ai_takes_free_material():
    from app import best_move
    import chess

    board = chess.Board("4k3/8/8/3q4/8/8/5K2/3R4 b - - 0 1")
    assert best_move(board, chess.BLACK) == chess.Move.from_uci("d5d1")


def test_ai_finds_mate_in_one():
    from app import best_move
    import chess

    board = chess.Board("6k1/5ppp/8/8/8/8/8/R3K3 w - - 0 1")
    assert best_move(board, chess.WHITE) == chess.Move.from_uci("a1a8")


def test_ai_game_human_resign():
    a, ja = make_ai()
    a.emit("resign", {"code": ja["code"], "token": ja["token"]})
    assert events(a, "state")[-1]["result"] == {"winner": "b", "reason": "resignation"}

def test_completed_game_is_saved_with_move_history():
    a, b, ja, jb = make_pair()
    move(a, ja, "e2", "e4")
    move(b, jb, "e7", "e5")
    a.emit("resign", {"code": ja["code"], "token": ja["token"]})
    with app.app_context():
        record = db.session.query(GameRecord).filter_by(room_code=ja["code"]).one()
        assert record.ended_at is not None
        assert record.result == "resignation"
        assert record.winner_id == jb["user_id"]
        assert [m.san for m in record.moves] == ["e4", "e5"]


def test_history_api_returns_completed_games():
    a, b, ja, jb = make_pair()
    move(a, ja, "e2", "e4")
    a.emit("resign", {"code": ja["code"], "token": ja["token"]})
    response = app.test_client().get(
        f"/api/history/{ja['user_id']}",
        headers={"Authorization": "Bearer " + ja["auth_token"]},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["games"]) == 1
    assert payload["games"][0]["opponent"] == jb["username"]
    assert payload["games"][0]["outcome"] == "loss"
    assert payload["games"][0]["moves"] == ["e4"]


def test_username_requires_auth_token_for_existing_user():
    a = socketio.test_client(app)
    a.emit("create_game", {"username": "SecureUser"})
    joined = events(a, "joined")[0]
    b = socketio.test_client(app)
    b.emit("create_game", {"user_id": joined["user_id"], "username": joined["username"]})
    error = events(b, "error_message")[0]
    assert error["fatal"] is True


def test_username_auth_token_allows_reconnect():
    a = socketio.test_client(app)
    a.emit("create_game", {"username": "SecureUser"})
    joined = events(a, "joined")[0]
    assert joined["auth_token"]
    a.disconnect()
    b = socketio.test_client(app)
    b.emit("create_game", {"user_id": joined["user_id"], "username": joined["username"], "auth_token": joined["auth_token"]})
    joined_again = events(b, "joined")[0]
    assert joined_again["user_id"] == joined["user_id"]


def test_wrong_auth_token_cannot_impersonate_user():
    a = socketio.test_client(app)
    a.emit("create_game", {"username": "SecureUser"})
    joined = events(a, "joined")[0]
    b = socketio.test_client(app)
    b.emit("create_game", {"user_id": joined["user_id"], "username": joined["username"], "auth_token": "wrong"})
    error = events(b, "error_message")[0]
    assert error["fatal"] is True


def test_leave_blocked_during_active_match():
    a, b, ja, jb = make_pair()
    a.emit("leave_game", {"code": ja["code"], "token": ja["token"]})
    error = events(a, "error_message")[0]
    assert error["message"] == "Resign before leaving the match"
    assert games[ja["code"]].sids[chess.WHITE] == a.sid


def test_leave_allowed_after_resignation():
    a, b, ja, jb = make_pair()
    a.emit("resign", {"code": ja["code"], "token": ja["token"]})
    a.get_received()
    a.emit("leave_game", {"code": ja["code"], "token": ja["token"]})
    assert games[ja["code"]].sids[chess.WHITE] is None


def test_history_api_requires_auth_token():
    a = socketio.test_client(app)
    a.emit("create_game", {"username": "HistoryUser"})
    joined = events(a, "joined")[0]
    response = app.test_client().get(f"/api/history/{joined['user_id']}")
    assert response.status_code == 401
