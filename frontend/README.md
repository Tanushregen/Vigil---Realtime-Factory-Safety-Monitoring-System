# VIGIL Frontend

The VIGIL frontend is a React application built with Vite. It provides the web interface for camera monitoring, recorder setup, safety rules, alerts, and settings.

## Requirements

- Node.js `^20.19.0` or `>=22.12.0` and npm (required by Vite 8)
- The VIGIL backend running at `http://127.0.0.1:8000`
- go2rtc running for camera stream and frame features

For complete project requirements and backend/go2rtc setup, see the [project README](../README.md).

## Install

From this directory, install the frontend dependencies:

```powershell
npm install
```

## Run in development

Start the backend and go2rtc first, following the [project setup instructions](../README.md). Then, from the `frontend` directory, run:

```powershell
npm run dev
```

Open the local URL printed by Vite (usually `http://localhost:5173`). The Vite development server proxies API requests to the backend on port `8000`.

## Build and lint

```powershell
npm run build
npm run lint
```

To serve a production build locally:

```powershell
npm run preview
```
