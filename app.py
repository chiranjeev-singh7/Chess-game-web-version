import math
import os
import secrets
import time
import uuid
import hashlib

import chess
from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__, static_folder="static", static_url_path="")

database_url = os.environ.get("DATABASE_URL", "sqlite:///chessduel.db")

if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(
        db.String(30),
        unique=True,
        nullable=False,
        index=True
    )
    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        nullable=False
    )


class UserCredential(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )
    token_hash = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        index=True
    )
    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        nullable=False
    )


class GameRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(
        db.String(5),
        unique=True,
        nullable=False,
        index=True
    )
    white_player_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False,
        index=True
    )
    black_player_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True,
        index=True
    )
    winner_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True,
        index=True
    )
    result = db.Column(db.String(20), nullable=True)
    mode = db.Column(
        db.String(10),
        nullable=False,
        default="online"
    )
    started_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        nullable=False
    )
    ended_at = db.Column(
        db.DateTime,
        nullable=True
    )

    moves = db.relationship(
        "GameMove",
        backref="game",
        cascade="all, delete-orphan",
        order_by="GameMove.move_number"
    )


class GameMove(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    game_id = db.Column(
        db.Integer,
        db.ForeignKey("game_record.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    move_number = db.Column(db.Integer, nullable=False)
    player_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=True,
        index=True
    )
    san = db.Column(db.String(20), nullable=False)
    uci = db.Column(db.String(10), nullable=False)
    fen = db.Column(db.String(100), nullable=False)
    created_at = db.Column(
        db.DateTime,
        server_default=db.func.now(),
        nullable=False
    )


with app.app_context():
    db.create_all()


origins = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "").split(",")
    if o.strip()
] or None

socketio = SocketIO(
    app,
    async_mode="gevent",
    cors_allowed_origins=origins
)


GAME_TTL = 60 * 60 * 3

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 5

PROMOTIONS = {"q", "r", "b", "n"}

AI_DEPTH = 3

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


games = {}
sid_index = {}
cleanup_started = False


class Game:
    def __init__(self):
        self.board = chess.Board()

        self.tokens = {
            chess.WHITE: None,
            chess.BLACK: None
        }

        self.user_ids = {
            chess.WHITE: None,
            chess.BLACK: None
        }

        self.usernames = {
            chess.WHITE: None,
            chess.BLACK: None
        }

        self.sids = {
            chess.WHITE: None,
            chess.BLACK: None
        }

        self.san = []
        self.last_move = None
        self.resigned = None
        self.ai_color = None
        self.updated = time.time()
        self.db_game_id = None
        self.history_saved = False

    def seat_of(self, token):
        if not isinstance(token, str):
            return None

        for color, seat_token in self.tokens.items():
            if seat_token and secrets.compare_digest(
                seat_token,
                token
            ):
                return color

        return None

    def both_joined(self):
        return all(self.tokens.values())

    def is_over(self):
        return (
            self.resigned is not None
            or self.board.is_game_over(claim_draw=True)
        )

    def snapshot(self):
        result = None

        if self.resigned is not None:
            result = {
                "winner": "b"
                if self.resigned == chess.WHITE
                else "w",
                "reason": "resignation"
            }

        else:
            outcome = self.board.outcome(claim_draw=True)

            if outcome:
                winner = (
                    None
                    if outcome.winner is None
                    else ("w" if outcome.winner else "b")
                )

                result = {
                    "winner": winner,
                    "reason": outcome.termination.name.lower()
                }

        return {
            "players": {
                "w": self.user_ids[chess.WHITE],
                "b": self.user_ids[chess.BLACK],
            },

            "usernames": {
                "w": self.usernames[chess.WHITE],
                "b": self.usernames[chess.BLACK],
            },

            "fen": self.board.fen(),

            "turn": "w" if self.board.turn else "b",

            "history": self.san,

            "last_move": self.last_move,

            "in_check": self.board.is_check(),

            "vs_ai": self.ai_color is not None,

            "result": result,

            "joined": {
                "w": self.tokens[chess.WHITE] is not None,
                "b": self.tokens[chess.BLACK] is not None,
            },

            "online": {
                "w": self.sids[chess.WHITE] is not None,
                "b": self.sids[chess.BLACK] is not None,
            },
        }


def hash_user_token(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def get_or_create_user(data):
    if not isinstance(data, dict):
        return None, None, "Invalid request"

    user_id = data.get("user_id")

    username = str(
        data.get("username", "")
    ).strip()

    auth_token = data.get("auth_token")

    if user_id:
        user = db.session.get(User, user_id)

        if not user:
            return None, None, "User account not found"

        if username.lower() != user.username.lower():
            return None, None, "Username does not match the account"

        if not isinstance(auth_token, str) or not auth_token:
            return None, None, "Authentication required"

        credential = UserCredential.query.filter_by(
            user_id=user.id
        ).first()

        if not credential:
            return None, None, "Authentication record not found"

        if not secrets.compare_digest(
            credential.token_hash,
            hash_user_token(auth_token)
        ):
            return None, None, "Authentication failed. Please create a new profile"

        return user, auth_token, None

    if not username:
        return None, None, "Enter a username"

    if len(username) < 3 or len(username) > 30:
        return None, None, "Enter a username between 3 and 30 characters"

    existing = User.query.filter(
        db.func.lower(User.username) == username.lower()
    ).first()

    if existing:
        return None, None, "Username is already taken"

    auth_token = secrets.token_urlsafe(32)

    user = User(username=username)

    db.session.add(user)

    db.session.flush()

    db.session.add(
        UserCredential(
            user_id=user.id,
            token_hash=hash_user_token(auth_token)
        )
    )

    db.session.commit()

    return user, auth_token, None


def create_game_record(
    game,
    room_code,
    white_user,
    mode="online"
):
    record = GameRecord(
        room_code=room_code,
        white_player_id=white_user.id,
        mode=mode
    )

    db.session.add(record)

    db.session.commit()

    game.db_game_id = record.id

    return record


def save_move(game, move, player_id):
    if game.db_game_id is None:
        return

    record = db.session.get(
        GameRecord,
        game.db_game_id
    )

    if not record:
        return

    db.session.add(
        GameMove(
            game_id=record.id,
            move_number=len(game.san),
            player_id=player_id,
            san=game.san[-1],
            uci=move.uci(),
            fen=game.board.fen(),
        )
    )

    db.session.commit()


def finalize_game(game):
    if (
        game.history_saved
        or not game.is_over()
        or game.db_game_id is None
    ):
        return

    record = db.session.get(
        GameRecord,
        game.db_game_id
    )

    if not record or record.ended_at is not None:
        game.history_saved = True
        return

    result = game.snapshot()["result"]

    record.result = (
        result["reason"]
        if result
        else None
    )

    if result and result["winner"]:
        winner_color = (
            chess.WHITE
            if result["winner"] == "w"
            else chess.BLACK
        )

        record.winner_id = game.user_ids[
            winner_color
        ]

    else:
        record.winner_id = None

    from datetime import datetime, timezone

    record.ended_at = datetime.now(timezone.utc)

    db.session.commit()

    game.history_saved = True


def evaluate_board(board, ai_color):
    if board.is_checkmate():
        return (
            -9999
            if board.turn == ai_color
            else 9999
        )

    if (
        board.is_stalemate()
        or board.is_insufficient_material()
    ):
        return 0

    score = 0

    for square in chess.SQUARES:
        piece = board.piece_at(square)

        if piece:
            value = PIECE_VALUES[
                piece.piece_type
            ]

            score += (
                value
                if piece.color == ai_color
                else -value
            )

    return score


def minimax(
    board,
    depth,
    alpha,
    beta,
    maximizing,
    ai_color
):
    if depth == 0 or board.is_game_over():
        return evaluate_board(
            board,
            ai_color
        ), None

    best = None

    if maximizing:
        best_score = -math.inf

        for move in list(board.legal_moves):
            board.push(move)

            score, _ = minimax(
                board,
                depth - 1,
                alpha,
                beta,
                False,
                ai_color
            )

            board.pop()

            if score > best_score:
                best_score = score
                best = move

            alpha = max(alpha, score)

            if beta <= alpha:
                break

        return best_score, best

    best_score = math.inf

    for move in list(board.legal_moves):
        board.push(move)

        score, _ = minimax(
            board,
            depth - 1,
            alpha,
            beta,
            True,
            ai_color
        )

        board.pop()

        if score < best_score:
            best_score = score
            best = move

        beta = min(beta, score)

        if beta <= alpha:
            break

    return best_score, best


def best_move(board, ai_color):
    _, move = minimax(
        board.copy(stack=False),
        AI_DEPTH,
        -math.inf,
        math.inf,
        board.turn == ai_color,
        ai_color
    )

    return move


def play(game, move):
    player_id = game.user_ids[
        game.board.turn
    ]

    game.san.append(
        game.board.san(move)
    )

    game.board.push(move)

    save_move(
        game,
        move,
        player_id
    )

    game.last_move = {
        "from": chess.square_name(
            move.from_square
        ),
        "to": chess.square_name(
            move.to_square
        ),
    }

    game.updated = time.time()


def new_code():
    while True:
        code = "".join(
            secrets.choice(CODE_ALPHABET)
            for _ in range(CODE_LENGTH)
        )

        if code not in games:
            return code


def lookup(data):
    if not isinstance(data, dict):
        return None, None, None

    code = str(
        data.get("code", "")
    ).strip().upper()

    game = games.get(code)

    color = (
        game.seat_of(data.get("token"))
        if game
        else None
    )

    return code, game, color


def cleanup_loop():
    while True:
        socketio.sleep(600)

        cutoff = time.time() - GAME_TTL

        for code in [
            c
            for c, g in games.items()
            if g.updated < cutoff
        ]:
            games.pop(code, None)


@app.route("/")
def index():
    return app.send_static_file(
        "index.html"
    )


@app.route("/health")
def health():
    return {
        "status": "ok",
        "games": len(games)
    }


@app.route("/api/history/<int:user_id>")
def history(user_id):
    auth_header = request.headers.get(
        "Authorization",
        ""
    )

    auth_token = (
        auth_header[7:]
        if auth_header.startswith("Bearer ")
        else ""
    )

    user = db.session.get(
        User,
        user_id
    )

    credential = (
        UserCredential.query.filter_by(
            user_id=user_id
        ).first()
        if user
        else None
    )

    if (
        not user
        or not credential
        or not auth_token
        or not secrets.compare_digest(
            credential.token_hash,
            hash_user_token(auth_token)
        )
    ):
        return {
            "error": "Unauthorized"
        }, 401

    records = GameRecord.query.filter(
        db.or_(
            GameRecord.white_player_id == user_id,
            GameRecord.black_player_id == user_id
        ),
        GameRecord.ended_at.isnot(None),
    ).order_by(
        GameRecord.ended_at.desc()
    ).limit(50).all()

    result = []

    for record in records:
        if record.white_player_id == user_id:
            opponent = (
                db.session.get(
                    User,
                    record.black_player_id
                )
                if record.black_player_id
                else None
            )

            color = "w"

        else:
            opponent = db.session.get(
                User,
                record.white_player_id
            )

            color = "b"

        if record.winner_id is None:
            outcome = "draw"

        elif record.winner_id == user_id:
            outcome = "win"

        else:
            outcome = "loss"

        result.append({
            "id": record.id,
            "room_code": record.room_code,
            "opponent": (
                opponent.username
                if opponent
                else "Computer"
            ),
            "color": color,
            "outcome": outcome,
            "reason": record.result,
            "mode": record.mode,
            "moves": [
                move.san
                for move in record.moves
            ],
            "move_count": len(
                record.moves
            ),
            "started_at": (
                record.started_at.isoformat()
                if record.started_at
                else None
            ),
            "ended_at": (
                record.ended_at.isoformat()
                if record.ended_at
                else None
            ),
        })

    return {
        "games": result
    }


@socketio.on("connect")
def on_connect():
    global cleanup_started

    if not cleanup_started:
        cleanup_started = True

        socketio.start_background_task(
            cleanup_loop
        )


@socketio.on("disconnect")
def on_disconnect(*args):
    entry = sid_index.pop(
        request.sid,
        None
    )

    if not entry:
        return

    code, color = entry

    game = games.get(code)

    if (
        game
        and game.sids[color] == request.sid
    ):
        game.sids[color] = None

        emit(
            "state",
            game.snapshot(),
            to=code
        )


@socketio.on("create_game")
def on_create(data=None):
    user, auth_token, error = get_or_create_user(
        data
    )

    if not user:
        emit(
            "error_message",
            {
                "message": error,
                "fatal": True
            }
        )
        return

    code = new_code()

    game = Game()

    token = uuid.uuid4().hex

    game.tokens[chess.WHITE] = token

    game.user_ids[chess.WHITE] = user.id

    game.usernames[chess.WHITE] = user.username

    game.sids[chess.WHITE] = request.sid

    if (
        isinstance(data, dict)
        and data.get("mode") == "ai"
    ):
        game.ai_color = chess.BLACK

        game.tokens[chess.BLACK] = (
            uuid.uuid4().hex
        )

        game.user_ids[chess.BLACK] = None

        game.sids[chess.BLACK] = "ai"

    games[code] = game

    create_game_record(
        game,
        code,
        user,
        "ai"
        if game.ai_color is not None
        else "online"
    )

    sid_index[request.sid] = (
        code,
        chess.WHITE
    )

    join_room(code)

    emit(
        "joined",
        {
            "code": code,
            "token": token,
            "auth_token": auth_token,
            "color": "w",
            "user_id": user.id,
            "username": user.username,
            "state": game.snapshot()
        }
    )


@socketio.on("join_game")
def on_join(data):
    user, auth_token, error = get_or_create_user(
        data
    )

    if not user:
        emit(
            "error_message",
            {
                "message": error,
                "fatal": True
            }
        )
        return

    if not isinstance(data, dict):
        emit(
            "error_message",
            {
                "message": "Invalid request",
                "fatal": True
            }
        )
        return

    code, game, color = lookup(data)

    if not game:
        emit(
            "error_message",
            {
                "message": "Game not found",
                "fatal": True
            }
        )
        return

    token = data.get("token")

    if color is None:
        if game.tokens[chess.BLACK] is not None:
            emit(
                "error_message",
                {
                    "message": "Game is full",
                    "fatal": True
                }
            )
            return

        color = chess.BLACK

        token = uuid.uuid4().hex

        game.tokens[color] = token

        game.user_ids[color] = user.id

        game.usernames[color] = user.username

        if game.db_game_id is not None:
            record = db.session.get(
                GameRecord,
                game.db_game_id
            )

            if record:
                record.black_player_id = user.id

                db.session.commit()

    game.sids[color] = request.sid

    game.updated = time.time()

    sid_index[request.sid] = (
        code,
        color
    )

    join_room(code)

    emit(
        "joined",
        {
            "code": code,
            "token": token,
            "auth_token": auth_token,
            "color": (
                "w"
                if color == chess.WHITE
                else "b"
            ),
            "user_id": user.id,
            "username": user.username,
            "state": game.snapshot()
        }
    )

    emit(
        "state",
        game.snapshot(),
        to=code,
        include_self=False
    )


@socketio.on("move")
def on_move(data):
    code, game, color = lookup(data)

    if not game or color is None:
        emit(
            "error_message",
            {
                "message": "Not part of this game",
                "fatal": True
            }
        )
        return

    if not game.both_joined():
        emit(
            "error_message",
            {
                "message": "Waiting for opponent"
            }
        )
        return

    if game.is_over():
        emit(
            "error_message",
            {
                "message": "Game is over"
            }
        )
        return

    if game.board.turn != color:
        emit(
            "error_message",
            {
                "message": "Not your turn"
            }
        )

        emit(
            "state",
            game.snapshot()
        )

        return

    promotion = data.get(
        "promotion"
    )

    if (
        promotion is not None
        and promotion not in PROMOTIONS
    ):
        emit(
            "error_message",
            {
                "message": "Invalid promotion"
            }
        )

        emit(
            "state",
            game.snapshot()
        )

        return

    uci = (
        f"{data.get('from')}"
        f"{data.get('to')}"
        f"{promotion or ''}"
    )

    try:
        move = chess.Move.from_uci(
            uci
        )

    except ValueError:
        emit(
            "error_message",
            {
                "message": "Invalid move"
            }
        )

        emit(
            "state",
            game.snapshot()
        )

        return

    if move not in game.board.legal_moves:
        emit(
            "error_message",
            {
                "message": "Illegal move"
            }
        )

        emit(
            "state",
            game.snapshot()
        )

        return

    play(
        game,
        move
    )

    finalize_game(game)

    emit(
        "state",
        game.snapshot(),
        to=code
    )

    if (
        game.ai_color is not None
        and not game.is_over()
    ):
        socketio.sleep(0.3)

        reply = best_move(
            game.board,
            game.ai_color
        )

        if reply:
            play(
                game,
                reply
            )

            finalize_game(game)

            emit(
                "state",
                game.snapshot(),
                to=code
            )


@socketio.on("resign")
def on_resign(data):
    code, game, color = lookup(data)

    if not game or color is None:
        emit(
            "error_message",
            {
                "message": "Not part of this game",
                "fatal": True
            }
        )
        return

    if (
        not game.both_joined()
        or game.is_over()
    ):
        return

    game.resigned = color

    game.updated = time.time()

    finalize_game(game)

    emit(
        "state",
        game.snapshot(),
        to=code
    )


@socketio.on("leave_game")
def on_leave(data):
    code, game, color = lookup(data)

    if not game or color is None:
        return

    if (
        game.both_joined()
        and not game.is_over()
    ):
        emit(
            "error_message",
            {
                "message": "Resign before leaving the match"
            }
        )
        return

    if game.sids[color] == request.sid:
        game.sids[color] = None

    sid_index.pop(
        request.sid,
        None
    )

    leave_room(code)

    emit(
        "state",
        game.snapshot(),
        to=code
    )


if __name__ == "__main__":
    socketio.run(
        app,
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                5000
            )
        ),
        debug=os.environ.get(
            "FLASK_DEBUG"
        ) == "1",
    )