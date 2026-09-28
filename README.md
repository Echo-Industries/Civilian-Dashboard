# Seattle Civilian Portal

The Civilian Portal is a Flask web application customized for a Seattle, Washington ER:LC server. It provides a Roblox-authenticated civilian view of player status, civilian careers, vehicle records, district maps, private 911 calls, private Department of Transportation roadside assistance requests, and an ER:LC-backed wanted-player board.

## Features

- Roblox OAuth sign-in with a signed Flask session.
- Required sign-in page with a Roblox headshot account menu and sign-out action.
- ER:LC server status proxy for the signed-in player's cash, job, GPS location, and connection status.
- Dashboard FBI 10 Most Wanted board for active server players explicitly marked wanted by ER:LC.
- Automatic vehicle registration from the signed-in player's currently spawned ER:LC vehicle.
- Civilian-team authorization for vehicle registration, 911 calls, and roadside assistance.
- Civilian-only career listings: Civilian, Postal Worker, Tow Truck Driver, Taxi Driver, Bus Driver, and Trucker.
- Private 911 and DOT call records stored in SQLite and keyed by Roblox user ID.
- Server-side filtering so civilians receive only ER:LC calls matching their Roblox ID or username.
- Roadside Assistance tab for tow, jump start, fuel delivery, and roadside recovery requests.
- DMV vehicle registry stored locally in the browser.
- District map layers backed by the image files in `static/images/`.
- Shared portal logo used in the header and browser-tab favicon.

## Requirements

- Python 3.10 or newer
- A Roblox OAuth application
- An ER:LC server API key for live server status

Install dependencies with:

```bash
python -m pip install -r requirements.txt
```

## Configuration

Copy the example environment file and fill in the values:

```bash
cp .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `ERLC_SERVER_KEY` | ER:LC server API key used by `/api/erlc/status`. |
| `ROBLOX_CLIENT_ID` | Roblox OAuth client ID. |
| `ROBLOX_CLIENT_SECRET` | Roblox OAuth client secret. Keep this private. |
| `ROBLOX_REDIRECT_URI` | OAuth callback URL, such as `http://localhost:5000/auth/roblox/callback`. |
| `FLASK_SECRET_KEY` | Optional stable secret for signing sessions. A random key is generated when omitted. |
| `DATABASE_PATH` | Optional SQLite path. Defaults to `civilian_portal.db`. |
| `PORT` | Optional web port. Defaults to `5000`. |

Register the callback URL with the Roblox OAuth application:

```text
http://localhost:5000/auth/roblox/callback
```

## Run locally

```bash
python app.py
```

Open `http://localhost:5000` in a browser. The Flask development server listens on all interfaces so forwarded development-container ports work as well.

## API overview

### `GET /api/auth/me`

Returns the current Roblox session user, or `null` when signed out.

### `GET /api/erlc/status`

Proxies ER:LC server status and returns the signed-in player's normalized profile and wanted-player entries. The `emergency_calls` array contains only calls belonging to that player; the raw server payload is not returned. The wanted list includes only players explicitly marked wanted in the ER:LC player data and is limited to ten entries. If the ER:LC response does not include wanted-status fields, the dashboard reports that the data is unavailable.

### `GET /api/calls`

Requires Roblox sign-in and returns only the signed-in user's saved 911 and DOT calls.

### `GET /api/vehicles`

Requires Roblox sign-in and returns vehicle registrations owned by the signed-in Roblox user.

### `POST /api/vehicles`

Requires Roblox sign-in, verifies that the user is currently in the ER:LC server on the Civilian team, finds their currently spawned vehicle from the ER:LC vehicle payload, and registers it automatically. Plate, model, and color are never accepted from the browser. A vehicle must be spawned before registration is allowed.

### `POST /api/calls`

Requires Roblox sign-in. Accepts JSON like:

```json
{
	"category": "DOT / Roadside Assistance",
	"description": "Vehicle needs a tow from the highway shoulder.",
	"location": "Highway 55"
}
```

The only accepted categories are `911 Emergency` and `DOT / Roadside Assistance`. Caller ID and caller name always come from the authenticated session, never from browser input.

## Project layout

```text
app.py                         Flask routes, OAuth, ER:LC proxy, and SQLite calls
requirements.txt               Python dependencies
templates/index.html            Portal UI, tabs, modals, and browser-side state
static/images/                 Logo and district/job map overlays
.env.example                   Configuration template
```

## Privacy model

Saved-call visibility is enforced on the server. A browser cannot request another user's calls by changing a parameter because the query uses the authenticated session's Roblox ID. ER:LC live calls are filtered against known caller ID and username fields before return. Keep authentication enabled in deployment and keep the Flask secret private.

## Production notes

Use a production WSGI server such as Gunicorn behind HTTPS rather than Flask's development server. Set a strong `FLASK_SECRET_KEY`, keep `.env` out of source control, and use a persistent writable `DATABASE_PATH` so saved call records survive restarts.