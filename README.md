# VIGIL

Real-time factory safety monitoring using existing CCTV, selective AI inference, deterministic rules, and auditable alerts.

VIGIL is a factory camera monitoring app. It provides a web dashboard for managing cameras and DVR/NVR recorders, checking camera status, viewing camera frames, and configuring camera groups and safety prompts.

## Features

- View camera status and filter cameras by zone.
- Add DVR/NVR recorders and create cameras from recorder channels.
- Add an individual camera using its RTSP stream.
- View camera frames and manage camera details.
- Organize cameras into groups and configure universal, group, and individual safety prompts.
- Sign in with administrator or standard-user access; administrators can manage user accounts.
- Use optional email-based password recovery when SMTP is configured.
- Store recorder and camera configuration in a local SQLite database.
- Use go2rtc to register and restream camera RTSP streams.

The Alerts page and Settings tabs are currently placeholders.

## Project structure

```text
backend/
  app/                 FastAPI API and camera/recorder logic
    auth/              Sign-in, sessions, password recovery, and user management
  vigil.db             SQLite database (created when the backend starts)
frontend/
  src/                 React application
go2rtc/
  go2rtc.yaml          go2rtc configuration
docker-compose.yml     go2rtc service
```

## Requirements

- Python 3.10 or later
- Node.js `^20.19.0` or `>=22.12.0` and npm (required by Vite 8)
- Docker Desktop with Docker Compose
- FFmpeg tools, including `ffprobe`, on your PATH for RTSP stream validation
- Network access from the machine running VIGIL to the cameras/DVRs

### Install FFmpeg on Windows

If `winget` is available, install FFmpeg with:

```powershell
winget install --id Gyan.FFmpeg.Shared --exact
```

Open a new terminal and verify that `ffprobe` is available:

```powershell
ffprobe -version
```

If the command is not found, add the FFmpeg `bin` directory to your Windows `PATH`, then open a new terminal.

## Ports

| Service | Default port | Purpose |
| --- | ---: | --- |
| Frontend (Vite) | `5173` | Web application |
| Backend (FastAPI) | `8000` | API |
| go2rtc | `1984` | Stream management API and frames |
| go2rtc | `8554` | RTSP restreaming |
| Cameras/DVRs | Usually `554` | RTSP access; the configured recorder port may differ |

Make sure these ports are available on the VIGIL machine. The backend host must also be able to reach each camera or recorder over its configured RTSP port.

## Setup

Run these commands from the project root in PowerShell.

### 1. Configure the encryption key

Copy the example environment file at the project root:

```powershell
Copy-Item .env.example .env
```

The backend requires a Fernet key in `.env` to encrypt recorder and camera credentials.

Generate a key:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add the generated value to `.env`:

```text
FERNET_KEY=your-generated-key
```

Keep this key private and backed up. Existing encrypted credentials cannot be decrypted if the key is lost or changed.

Also add a secret for signing login sessions (at least 32 characters) to `.env`:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

```text
JWT_SECRET_KEY=your-generated-secret
```

See `.env.example` for the optional settings (session length, HTTPS cookies, login lockout, SMTP for password reset).

### 2. Install backend dependencies

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r backend\requirements.txt
```

### 3. Start go2rtc

```powershell
docker compose up -d
```

go2rtc exposes its API on port `1984` and RTSP restreaming on port `8554`.

### 4. Start the backend

In a new PowerShell terminal from the project root:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
```

The API is available at `http://127.0.0.1:8000`. Interactive API documentation is at `http://127.0.0.1:8000/docs`.

The SQLite database is stored at `backend/vigil.db` and its tables are initialized when the backend starts.

### 5. Start the frontend

In another PowerShell terminal:

```powershell
Set-Location frontend
npm install
npm run dev
```

Open the local address printed by Vite (by default, `http://localhost:5173`). The Vite development server proxies `/api` requests to the backend on port `8000`.

## Authentication

Every API route requires a signed-in user, except `/`, `/health` and the public sign-in endpoints under `/api/auth`. There is no public registration: accounts are created by an administrator.

### Create the first administrator

Run once from the `backend` directory. You will be asked for the password:

```powershell
Set-Location backend
..\.venv\Scripts\python -m app.auth.create_admin --name "Your Name" --email you@company.com
```

The command refuses to run once an administrator exists. Then sign in at `http://localhost:5173`.

### Manage users

Administrators see a **Users** page in the sidebar to create users, change roles, reset passwords and disable accounts. The same actions are available through `POST /api/auth/users`, `GET /api/auth/users` and `PATCH /api/auth/users/{user_id}`.

### Sessions

- Passwords are hashed with Argon2.
- Sessions are JWTs stored in an HttpOnly, SameSite=Lax cookie (8 hours, or 7 days with "Remember me"). Set `COOKIE_SECURE=true` when serving over HTTPS.
- Logging out, changing a password or disabling a user ends that session immediately.
- After 5 failed sign-ins for an email (or 30 from an IP address), further attempts are blocked for 15 minutes.
- "Forgot password" emails a single-use link valid for 30 minutes, only when `SMTP_HOST` and `SMTP_FROM` are configured. Without SMTP, users are told to contact their administrator.

## Supported recorder URL generation

Automatic RTSP URL generation is configured for Hikvision, Dahua, and CP Plus recorders. Other recorder brands can use the manual camera RTSP setup where applicable.

## Development checks

Run from the `frontend` directory:

```powershell
npm run build
npm run lint
```

## Notes

- Camera streams must be reachable from the VIGIL backend host.
- `ffprobe` is used to validate RTSP streams when adding or configuring cameras.
- Do not commit `.env` or share its Fernet key.
