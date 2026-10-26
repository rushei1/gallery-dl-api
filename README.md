# gallery-api

Production-ready REST API that wraps [gallery-dl](https://github.com/mikf/gallery-dl) for external applications (e.g. a Node.js bot).

```
Node.js Bot
      │
HTTP Request (X-API-Key)
      │
      ▼
FastAPI Server (gallery-api)
      │
gallery-dl subprocess
      │
Downloads files → downloads/{job_id}/
      │
Returns metadata → Node bot fetches files via /file/{id}
```

## Prerequisites

- Python 3.11+
- `gallery-dl` available on PATH (installed via `pip install -r requirements.txt`)

## Installation

```bash
cd gallery-api
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate

pip install -r requirements.txt
```

Copy and edit `.env`:

```bash
cp .env .env.local   # optional
# Set API_KEY to a long random secret
```

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Or on **Pterodactyl**: see [PTERODACTYL.md](PTERODACTYL.md).

## Authentication

Every endpoint except `GET /health` requires the header:

```
X-API-Key: your_secret_key
```

Invalid or missing keys return `401 Unauthorized`.

## Endpoints

### `GET /`

```json
{"service": "gallery-api", "status": "online"}
```

### `GET /health` (no auth)

```json
{
  "status": "healthy",
  "uptime_seconds": 12.5,
  "active_jobs": 0,
  "queued_jobs": 0,
  "completed_jobs": 1,
  "failed_jobs": 0,
  "storage_bytes": 1048576
}
```

### `POST /download`

```bash
curl \
  -H "X-API-Key: your_key" \
  -H "Content-Type: application/json" \
  -X POST \
  http://localhost:8000/download \
  -d '{"url":"https://example.com"}'
```

Response:

```json
{"success": true, "job_id": "uuid"}
```

### `GET /status/{job_id}`

Returns `queued`, `downloading`, `completed`, or `failed` status.

### `GET /file/{file_id}`

Returns the file with correct MIME type (`FileResponse`).

### `DELETE /job/{job_id}`

Deletes job metadata and downloaded files immediately.

```json
{"success": true}
```

### `POST /job/{job_id}/complete`

Same as `DELETE` — call this **after your bot has sent all files** to remove temporary data from disk.

### Bonus endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /job/{job_id}/complete` | Purge job after bot finishes sending files |
| `POST /job/{job_id}/retry` | Retry a failed job |
| `GET /job/{job_id}/archive` | Download all job files as ZIP |
| `GET /stats` | Aggregate download statistics |

## Temporary file storage

Files are **not meant to be kept**. They live in `downloads/{job_id}/` only long enough for your bot to fetch and forward them.

**Recommended bot flow:**

1. `POST /download` → get `job_id`
2. Poll `GET /status/{job_id}` until `completed`
3. `GET /file/{file_id}` for each file → send to Discord (or elsewhere)
4. **`POST /job/{job_id}/complete`** → deletes all files and metadata

If the bot forgets step 4, a background cleanup removes expired jobs automatically (`JOB_EXPIRY_MINUTES`, default **5 minutes**).

## Node.js integration

```javascript
const GALLERY_API = process.env.GALLERY_API_URL;
const API_KEY = process.env.GALLERY_API_KEY;
const headers = { 'X-API-Key': API_KEY };

async function downloadAndSend(url, sendToDiscord) {
  const res = await fetch(`${GALLERY_API}/download`, {
    method: 'POST',
    headers: { ...headers, 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });
  const { job_id } = await res.json();

  // Poll until completed
  let files;
  while (true) {
    const statusRes = await fetch(`${GALLERY_API}/status/${job_id}`, { headers });
    const status = await statusRes.json();
    if (status.status === 'completed') {
      files = status.files;
      break;
    }
    if (status.status === 'failed') throw new Error(status.error);
    await new Promise((r) => setTimeout(r, 2000));
  }

  // Fetch each file and send it
  for (const file of files) {
    const fileRes = await fetch(`${GALLERY_API}/file/${file.id}`, { headers });
    const buffer = Buffer.from(await fileRes.arrayBuffer());
    await sendToDiscord(buffer, file.filename, file.mime);
  }

  // Remove temporary files from the server
  await fetch(`${GALLERY_API}/job/${job_id}/complete`, {
    method: 'POST',
    headers,
  });
}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | *(required)* | Shared secret for `X-API-Key` |
| `DOWNLOAD_DIR` | `downloads` | Directory for job files |
| `JOB_EXPIRY_MINUTES` | `5` | Safety-net auto-delete if bot never calls `/complete` |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Listen port (local dev) |
| `SERVER_PORT` | — | Pterodactyl-injected port (takes precedence) |
| `MAX_DOWNLOADS` | `5` | Max concurrent gallery-dl processes |
| `MAX_RETRIES` | `2` | Auto-retry failed downloads |
| `RATE_LIMIT_PER_MINUTE` | `30` | Per API key request limit |
| `GALLERY_DL_COOKIE_FILE` | — | Fallback cookies for other sites |
| `GALLERY_DL_YOUTUBE_COOKIE_FILE` | — | Cookies for YouTube URLs (`cookies.txt`) |
| `GALLERY_DL_INSTAGRAM_COOKIE_FILE` | — | Cookies for Instagram URLs (`insta-cookies.txt`) |

## Project structure

```
gallery-api/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── auth.py
│   ├── downloader.py
│   ├── models.py
│   ├── cleanup.py
│   ├── utils.py
│   ├── routes/
│   │   ├── download.py
│   │   ├── files.py
│   │   └── jobs.py
│   └── storage/
│       └── job_store.py
├── downloads/
├── requirements.txt
├── run.py            # Pterodactyl PY_FILE entry (Python Generic egg)
├── start.sh          # Alternative if egg startup is: bash start.sh
├── .env
├── README.md
└── PTERODACTYL.md
```

## Future: SQLite persistence

Job metadata is stored in-memory (`app/storage/job_store.py`). The `JobStoreProtocol` interface is designed so a SQLite backend can replace the in-memory dict without changing routes.

## Pterodactyl deployment

See **[PTERODACTYL.md](PTERODACTYL.md)** for a full step-by-step guide.
