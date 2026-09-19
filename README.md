# Chess Duel

Real time online chess. Create a game, share the room code or link, and play a friend from any device.

## Features

- Room code matchmaking with shareable invite link
- Live moves over WebSockets using Flask-SocketIO
- Server authoritative move validation with python-chess
- Isolated per game state, turn enforcement and player tokens
- Reconnect support so refreshing the page returns you to your seat
- Pawn promotion picker, check highlight, last move highlight and move list
- Checkmate, stalemate, draw rules and resignation
- Responsive layout for desktop and mobile
- Automatic cleanup of idle games
- Persistent user accounts and completed game history
- Stored move-by-move SAN, UCI and FEN history
- Game history API and lobby history viewer

## Stack

Python, Flask, Flask-SocketIO, Flask-SQLAlchemy, SQL, SQLite, PostgreSQL, gevent, python-chess, JavaScript, jQuery, chessboard.js, chess.js

## Run locally

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000 in two browser windows.

## Tests

```
pytest
```

## Deploy on Render

Build command

```
pip install -r requirements.txt
```

Start command

```
gunicorn -k geventwebsocket.gunicorn.workers.GeventWebSocketWorker -w 1 -b 0.0.0.0:$PORT app:app
```

Game state lives in memory so run a single worker. Set `CORS_ORIGINS` only if the frontend is hosted on a different domain.

## Socket events

| Event | Direction | Purpose |
| --- | --- | --- |
| create_game | client to server | Start a new room as white |
| join_game | client to server | Join or rejoin a room with code and token |
| move | client to server | Submit a move |
| resign | client to server | Resign the game |
| leave_game | client to server | Leave the room |
| joined | server to client | Seat assignment and initial state |
| state | server to client | Full game snapshot broadcast to the room |
| error_message | server to client | Rejected action |

## Database

The project now includes a persistent Users database using Flask-SQLAlchemy.

- Local development uses SQLite (`chessduel.db`).
- Deployment can use PostgreSQL through the `DATABASE_URL` environment variable.
- Each player receives a persistent `user_id` and unique username.
- User IDs are included in multiplayer game state and reconnect sessions.
- Usernames are validated to 3–30 characters and checked case-insensitively for duplicates.

### Local setup

```bash
pip install -r requirements.txt
python app.py
```

The SQLite database is created automatically when the application starts.

### Deployment

Set the `DATABASE_URL` environment variable to the PostgreSQL connection string supplied by the hosting provider.


### Game history database

Completed games are persisted in the database using three relational entities:

- `User`: persistent player identity and username
- `GameRecord`: room, players, game mode, result and timestamps
- `GameMove`: move number, player, SAN, UCI and resulting FEN

The application writes each accepted move to `GameMove` and finalizes `GameRecord` when a game ends by checkmate, draw, or resignation. The `/api/history/<user_id>` endpoint returns a player's completed games, opponents, outcomes and move history.

The lobby also includes a Game History viewer for the current stored user profile.

## User identity and match controls

- Usernames are unique and stored in the database.
- A new player receives a random authentication token. The server stores only its SHA-256 hash.
- Knowing another player's username or user ID is not sufficient to reuse that account; the authentication token is required for reconnects and game-history access.
- The Leave button is disabled during an active match. A player must resign before leaving.
- After a match ends, Leave becomes available.
- The Copy code button copies only the five-character room code.
