import os
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from dotenv import load_dotenv
import requests

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", secrets.token_hex(32))

ERLC_API_KEY = os.getenv("ERLC_SERVER_KEY")
PORT = int(os.getenv("PORT", 5000))
DATABASE_PATH = os.getenv("DATABASE_PATH", "civilian_portal.db")
ROBLOX_CLIENT_ID = os.getenv("ROBLOX_CLIENT_ID")
ROBLOX_CLIENT_SECRET = os.getenv("ROBLOX_CLIENT_SECRET")
ROBLOX_REDIRECT_URI = os.getenv("ROBLOX_REDIRECT_URI")


def get_db():
    database = sqlite3.connect(DATABASE_PATH)
    database.row_factory = sqlite3.Row
    database.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            payload TEXT NOT NULL,
            published_by_id TEXT NOT NULL,
            published_by_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    database.execute(
        """
        CREATE TABLE IF NOT EXISTS civilian_calls (
            id TEXT PRIMARY KEY,
            caller_id TEXT NOT NULL,
            caller_name TEXT NOT NULL,
            category TEXT NOT NULL,
            description TEXT NOT NULL,
            location TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    database.execute(
        """
        CREATE TABLE IF NOT EXISTS civilian_vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT NOT NULL,
            plate TEXT NOT NULL,
            model TEXT NOT NULL,
            color TEXT,
            registered_at TEXT NOT NULL,
            UNIQUE(owner_id, plate)
        )
        """
    )
    return database


def current_user():
    return session.get("roblox_user")


def login_required(handler):
    @wraps(handler)
    def wrapped(*args, **kwargs):
        if not current_user():
            return jsonify({"error": "Sign in with Roblox first."}), 401
        return handler(*args, **kwargs)

    return wrapped


@app.route("/")
def index():
    """Serves the main Civilian Portal frontend interface."""
    user = current_user()
    if not user:
        return redirect(url_for("sign_in"))
    _, player, error = _load_server_state(user)
    if error or not player or not _is_civilian(player):
        return render_template("access_denied.html"), 403
    return render_template("index.html")


@app.route("/sign-in")
def sign_in():
    if current_user():
        return redirect(url_for("index"))
    return render_template("sign_in.html")


@app.route("/auth/roblox/login")
def roblox_login():
    if not all((ROBLOX_CLIENT_ID, ROBLOX_CLIENT_SECRET, ROBLOX_REDIRECT_URI)):
        return jsonify({
            "error": "Configure ROBLOX_CLIENT_ID, ROBLOX_CLIENT_SECRET, and ROBLOX_REDIRECT_URI in your .env file."
        }), 500

    state = secrets.token_urlsafe(32)
    session["roblox_oauth_state"] = state
    params = {
        "client_id": ROBLOX_CLIENT_ID,
        "redirect_uri": ROBLOX_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid profile",
        "state": state,
    }
    authorization_url = requests.Request(
        "GET", "https://apis.roblox.com/oauth/v1/authorize", params=params
    ).prepare().url
    return redirect(authorization_url)


@app.route("/auth/roblox/callback")
@app.route("/auth/callback")
def roblox_callback():
    if request.args.get("state") != session.pop("roblox_oauth_state", None):
        return "Invalid Roblox OAuth state.", 400

    code = request.args.get("code")
    if not code:
        return f"Roblox sign-in failed: {request.args.get('error_description', 'No authorization code returned.')}", 400

    try:
        token_response = requests.post(
            "https://apis.roblox.com/oauth/v1/token",
            data={
                "client_id": ROBLOX_CLIENT_ID,
                "client_secret": ROBLOX_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": ROBLOX_REDIRECT_URI,
            },
            timeout=10,
        )
        token_response.raise_for_status()
        access_token = token_response.json()["access_token"]
        user_response = requests.get(
            "https://apis.roblox.com/oauth/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_response.raise_for_status()
        user_data = user_response.json()
    except (requests.RequestException, KeyError) as error:
        return f"Roblox sign-in failed: {error}", 502

    session["roblox_user"] = {
        "id": str(user_data["sub"]),
        "name": user_data.get("preferred_username") or user_data.get("name") or "Roblox user",
        "avatar_url": f"https://www.roblox.com/headshot-thumbnail/image?userId={user_data['sub']}&width=150&height=150&format=png",
    }
    return redirect(url_for("index"))


@app.route("/auth/logout")
def logout():
    session.pop("roblox_user", None)
    return redirect(url_for("index"))


@app.route("/api/auth/me")
def auth_me():
    return jsonify({"user": current_user()})


def _call_belongs_to_user(call, user):
    user_id = str(user.get("id", ""))
    username = str(user.get("name", "")).lower()
    for key in ("UserId", "UserID", "PlayerId", "PlayerID", "CreatorId", "CallerId", "AuthorId"):
        if call.get(key) is not None and str(call[key]) == user_id:
            return True
    for key in ("Username", "Player", "Creator", "Caller", "Author", "From"):
        value = call.get(key)
        if isinstance(value, dict):
            if str(value.get("Id") or value.get("UserId", "")) == user_id:
                return True
            value = value.get("Username") or value.get("Name")
        if value and str(value).split(":", 1)[0].lower() == username:
            return True
    return False


def _owned_saved_calls(user):
    database = get_db()
    rows = database.execute(
        "SELECT id, category, description, location, status, created_at FROM civilian_calls WHERE caller_id = ? ORDER BY created_at DESC",
        (str(user["id"]),),
    ).fetchall()
    database.close()
    return [
        {
            "id": row["id"],
            "type": row["category"],
            "desc": row["description"],
            "location": row["location"] or "Location unavailable",
            "status": row["status"],
            "timestamp": row["created_at"],
            "units": [],
        }
        for row in rows
    ]


def _player_matches_user(player, user):
    player_id = str(player.get("Id") or player.get("UserId") or player.get("PlayerId") or "")
    raw_player = str(player.get("Player", ""))
    parsed_username = raw_player.partition(":")[0]
    username = str(player.get("Username") or player.get("Name") or parsed_username)
    return player_id == str(user.get("id", "")) or username.lower() == str(user.get("name", "")).lower()


def _is_civilian(player):
    return str(player.get("Team") or player.get("Job") or player.get("team") or "").strip().lower() == "civilian"


def _vehicle_matches_user(vehicle, user):
    for key in ("OwnerId", "OwnerID", "UserId", "UserID", "PlayerId", "PlayerID"):
        if vehicle.get(key) is not None and str(vehicle[key]) == str(user.get("id", "")):
            return True
    for key in ("Owner", "Player", "Username", "Driver"):
        value = vehicle.get(key)
        if isinstance(value, dict):
            value = value.get("Username") or value.get("Name") or value.get("Id")
        if value and str(value).split(":", 1)[0].lower() == str(user.get("name", "")).lower():
            return True
    return False


def _current_vehicle(data, user):
    vehicles = data.get("Vehicles", [])
    if isinstance(vehicles, dict):
        vehicles = list(vehicles.values())
    for vehicle in vehicles:
        if not isinstance(vehicle, dict) or not _vehicle_matches_user(vehicle, user):
            continue
        return {
            "plate": str(vehicle.get("Plate") or vehicle.get("LicensePlate") or vehicle.get("Name") or "").strip(),
            "model": str(vehicle.get("Vehicle") or vehicle.get("Model") or vehicle.get("VehicleName") or vehicle.get("Name") or "Current vehicle"),
            "color": str(vehicle.get("Color") or vehicle.get("Paint") or "Unknown"),
        }
    return None


def _load_server_state(user):
    if not ERLC_API_KEY or ERLC_API_KEY == "your_actual_erlc_server_key_here":
        return None, None, ("ERLC_SERVER_KEY is not configured in your .env file.", 500)
    try:
        response = requests.get(
            "https://api.erlc.gg/v2/server?Players=true&Vehicles=true&EmergencyCalls=true",
            headers={"server-key": ERLC_API_KEY},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as error:
        return None, None, (f"Failed to connect to ER:LC API: {error}", 502)

    players = data.get("Players", [])
    current_player = None
    for player in players:
        if isinstance(player, str):
            username, _, player_id = player.partition(":")
            player = {"Username": username, "Player": player, "Id": player_id or None}
        if isinstance(player, dict) and _player_matches_user(player, user):
            current_player = player
            break
    return data, current_player, None


@app.route("/api/calls", methods=["GET", "POST"])
@login_required
def civilian_calls():
    user = current_user()
    server_data, player, error = _load_server_state(user)
    if error:
        return jsonify({"error": error[0]}), error[1]
    if not player:
        return jsonify({"error": "Your Roblox account is not currently in the ER:LC server."}), 403
    if not _is_civilian(player):
        return jsonify({"error": "Switch to the Civilian team in game to access this dashboard."}), 403

    if request.method == "GET":
        return jsonify({"calls": _owned_saved_calls(user)})

    payload = request.get_json(silent=True) or {}
    category = str(payload.get("category", "")).strip()
    description = str(payload.get("description", "")).strip()
    location = str(payload.get("location", "")).strip() or "Location unavailable"
    if category not in {"911 Emergency", "DOT / Roadside Assistance"} or not description:
        return jsonify({"error": "Choose a valid call type and provide a description."}), 400

    call = {
        "id": f"CIV-{secrets.token_hex(4).upper()}",
        "caller_id": str(user["id"]),
        "caller_name": user["name"],
        "category": category,
        "description": description,
        "location": location,
        "status": "Submitted",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    database = get_db()
    database.execute(
        "INSERT INTO civilian_calls (id, caller_id, caller_name, category, description, location, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(call.values()),
    )
    database.commit()
    database.close()
    return jsonify({"call": {"id": call["id"], "type": category, "desc": description, "location": location, "status": call["status"], "timestamp": call["created_at"], "units": []}}), 201


@app.route("/api/vehicles", methods=["GET", "POST"])
@login_required
def civilian_vehicles():
    user = current_user()
    server_data, player, error = _load_server_state(user)
    if error:
        return jsonify({"error": error[0]}), error[1]
    if not player:
        return jsonify({"error": "Your Roblox account is not currently in the ER:LC server."}), 403
    if not _is_civilian(player):
        return jsonify({"error": "Switch to the Civilian team in game to access this dashboard."}), 403

    database = get_db()
    if request.method == "GET":
        rows = database.execute(
            "SELECT plate, model, color, registered_at FROM civilian_vehicles WHERE owner_id = ? ORDER BY registered_at DESC",
            (str(user["id"]),),
        ).fetchall()
        database.close()
        return jsonify({"vehicles": [dict(row) for row in rows]})

    vehicle = _current_vehicle(server_data, user)
    if not vehicle or not vehicle["plate"]:
        database.close()
        return jsonify({"error": "Spawn your vehicle in ER:LC before registering it."}), 409
    try:
        database.execute(
            "INSERT INTO civilian_vehicles (owner_id, plate, model, color, registered_at) VALUES (?, ?, ?, ?, ?)",
            (str(user["id"]), vehicle["plate"], vehicle["model"], vehicle["color"], datetime.now(timezone.utc).isoformat()),
        )
        database.commit()
    except sqlite3.IntegrityError:
        database.close()
        return jsonify({"error": "That vehicle is already registered to your account."}), 409
    database.close()
    return jsonify({"vehicle": vehicle}), 201


@app.route("/api/erlc/status", methods=["GET"])
@login_required
def get_erlc_status():
    """Proxy endpoint to securely fetch live player lists, in-game cash, IDs, active units, and GPS emergency calls from ER:LC."""
    signed_in_user = current_user() or {}
    data, player, error = _load_server_state(signed_in_user)
    if error:
        return jsonify({"error": error[0]}), error[1]
    if not player or not _is_civilian(player):
        return jsonify({"error": "Dashboard access requires the Civilian team in ER:LC."}), 403
    current_player = None
    if player:
        raw_player = str(player.get("Player", ""))
        parsed_username, _, parsed_id = raw_player.partition(":")
        player_id = str(player.get("Id") or player.get("UserId") or player.get("PlayerId") or parsed_id)
        username = str(player.get("Username") or player.get("Name") or parsed_username)
        current_player = {
            "id": player_id or None,
            "username": username or None,
            "cash": player.get("Cash", player.get("Money", player.get("cash"))),
            "job": player.get("Team") or player.get("Job") or player.get("team"),
            "location": player.get("Location") or player.get("Position") or player.get("GPS"),
            "is_civilian": _is_civilian(player),
        }
    emergency_calls = data.get("EmergencyCalls", [])
    if isinstance(emergency_calls, dict):
        emergency_calls = list(emergency_calls.values())
    owned_emergency_calls = [
        call for call in emergency_calls
        if signed_in_user and isinstance(call, dict) and _call_belongs_to_user(call, signed_in_user)
    ]
    return jsonify({
        "api_connection": True,
        "current_player": current_player,
        "current_vehicle": _current_vehicle(data, signed_in_user) if signed_in_user else None,
        "emergency_calls": owned_emergency_calls,
        "saved_calls": _owned_saved_calls(signed_in_user) if signed_in_user else [],
    })


if __name__ == "__main__":
    print(f"[*] Starting Seattle, Washington Civilian Portal Backend on http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=True)