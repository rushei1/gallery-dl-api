# Pterodactyl Deployment Guide

This guide walks through deploying **gallery-api** on a [Pterodactyl Panel](https://pterodactyl.io/) server.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Pterodactyl Node (Wings)                       │
│                                                 │
│  ┌──────────────┐      HTTP + X-API-Key         │
│  │  Node.js Bot │ ──────────────────────────►   │
│  │  container   │      http://172.18.x.x:PORT   │
│  └──────────────┘                               │
│                          ┌──────────────────┐   │
│                          │  gallery-api     │   │
│                          │  container       │   │
│                          │  /home/container │   │
│                          └────────┬─────────┘   │
│                                   │ subprocess  │
│                                   ▼             │
│                              gallery-dl         │
│                                   │             │
│                                   ▼             │
│                         downloads/job_uuid/     │
└─────────────────────────────────────────────────┘
```

## Recommended server specs

| Resource | Minimum | Recommended |
|----------|---------|-------------|
| RAM | 512 MB | 1–2 GB |
| Disk | 5 GB | 20+ GB |
| CPU | 1 vCPU | 2 vCPU |
| Ports | 1 allocation | Same node as Node bot |

---

## Step 1 — Import a Python egg

**Option A (recommended):** Import a community Python egg:

- [Python Generic](https://eggs.pterodactyl.io/egg/generic-python-generic/)
- [Uvicorn/FastAPI gist](https://gist.github.com/isaackogan/f25e5bd044b551c24b56d1591aa6f724)

**Option B:** Create a custom egg:

- **Docker image:** `ghcr.io/pterodactyl/yolks:python_3.11`
- **Install script:** `pip install -r requirements.txt`
- **Startup detection (`config.startup.done`):** `Application startup complete`

---

## Step 2 — Create the server

1. **Admin → Servers → Create New**
2. Select your Python nest/egg
3. Assign a **port allocation** (becomes `SERVER_PORT`)
4. Set RAM/disk per table above
5. Place on the **same node** as your Node bot for internal networking

---

## Step 3 — Upload files

Upload the entire `gallery-api/` folder contents to `/home/container`:

```
/home/container/
├── app/
│   └── main.py       ← FastAPI app (not the PY_FILE entry)
├── downloads/
├── requirements.txt
├── run.py            ← set PY_FILE to this
└── .env              (optional — prefer panel variables)
```

**Do not upload `venv/`.**

Via SFTP, File Manager, or git clone if your egg supports `AUTO_UPDATE`.

---

## Step 4 — Configure startup (Python Generic egg)

If your egg startup looks like this:

```bash
... pip install ... -r ${REQUIREMENTS_FILE}; /usr/local/bin/python /home/container/${PY_FILE}
```

you **must** use a Python file for `PY_FILE`, **not** `start.sh` or `app.py`.

| Variable | Set to |
|----------|--------|
| **PY_FILE** | `run.py` |
| **REQUIREMENTS_FILE** | `requirements.txt` |

The panel field **"App py file"** (`PY_FILE`) must be **`run.py`**.

Why not `app.py`? This project uses an `app/` package folder; a root `app.py` would conflict with it. The real FastAPI app lives at `app/main.py`.

Why not `start.sh`? The generic egg runs `python ${PY_FILE}` — Python cannot execute shell scripts.

**Egg startup detection** (if server stuck on "Starting"):

```
Application startup complete
```

or:

```
Gallery API ready on
```

---

## Step 5 — Environment variables

Set in **Server → Startup → Environment Variables**:

| Variable | Example | Required |
|----------|---------|----------|
| `API_KEY` | `a1b2c3...` long random string | Yes |
| `DOWNLOAD_DIR` | `downloads` | No |
| `JOB_EXPIRY_MINUTES` | `5` | Safety-net cleanup if bot forgets `/complete` |
| `MAX_DOWNLOADS` | `5` | No |
| `HOST` | `0.0.0.0` | No |

`SERVER_PORT` is injected automatically by Pterodactyl — **do not set it manually**.

Mark `API_KEY` as not user-viewable if end users access the panel.

---

## Step 6 — Connect your Node.js bot

### Same node (recommended)

Use the **internal allocation IP** from the server's Allocations tab:

```
http://172.18.0.5:25565
```

On the Node bot server, set:

```
GALLERY_API_URL=http://172.18.0.5:25565
GALLERY_API_KEY=<same value as API_KEY>
```

### Public access

1. Set up Nginx/Caddy reverse proxy on the node
2. Point `gallery-api.yourdomain.com` → `127.0.0.1:SERVER_PORT`
3. Use HTTPS
4. Bot calls `https://gallery-api.yourdomain.com`

---

## Step 7 — Reverse proxy (optional)

```nginx
server {
    listen 443 ssl;
    server_name gallery-api.example.com;

    location / {
        proxy_pass http://127.0.0.1:25565;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 10m;
        proxy_read_timeout 600s;
    }
}
```

Replace `25565` with your allocated port.

---

## Step 8 — Verify

1. **Start** the server in the panel console
2. Look for: `Gallery API ready on 0.0.0.0:...`
3. Test health (no auth):

```bash
curl http://<ip>:<port>/health
```

4. Test download:

```bash
curl -H "X-API-Key: your_key" \
  -H "Content-Type: application/json" \
  -X POST http://<ip>:<port>/download \
  -d '{"url":"https://example.com/..."}'
```

5. Poll `/status/{job_id}` until `completed`
6. Fetch `/file/{file_id}`

---

## Step 9 — Cookies (YouTube + Instagram)

Upload both Netscape cookie files to `/home/container/`:

| File | Purpose |
|------|---------|
| `cookies.txt` | YouTube |
| `insta-cookies.txt` | Instagram |

Set in **Startup → Environment Variables**:

```
GALLERY_DL_YOUTUBE_COOKIE_FILE=/home/container/cookies.txt
GALLERY_DL_INSTAGRAM_COOKIE_FILE=/home/container/insta-cookies.txt
```

The API selects the correct file automatically from the download URL. Restart after uploading.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Stuck on **Starting** | Set egg done-string to `Application startup complete` |
| **Connection refused** | Use internal allocation IP + correct `SERVER_PORT` |
| `gallery-dl not found` | Re-upload `run.py`, `app/utils.py`, `app/downloader.py`; restart |
| **401 Unauthorized** | Ensure `API_KEY` matches on both servers exactly |
| **Disk full** | Lower `JOB_EXPIRY_MINUTES` or increase disk limit |
| Jobs lost on restart | Expected (in-memory store); bot should fetch files before expiry |
| Downloads fail | Test in console: `python -m gallery_dl -g URL` |
| Instagram/YouTube login errors | Upload cookie files and set env vars above |
| `HTTP redirect to home page` | Refresh `insta-cookies.txt`; Instagram often blocks hosting IPs — see below |

### Instagram: "HTTP redirect to home page"

This means Instagram **rejected the login session**, not that the API is broken.

**Fix (try in order):**

1. **Export fresh Instagram-only cookies** from Chrome/Firefox while logged into Instagram:
   - Use extension: "Get cookies.txt LOCALLY" (Chrome) or "Export Cookies" (Firefox)
   - Export **only** `instagram.com` cookies — not your entire browser cookie dump
   - Upload to `/home/container/insta-cookies.txt` and restart

2. **Verify cookies on the server:**
   ```bash
   grep -E 'sessionid|csrftoken' /home/container/insta-cookies.txt | grep instagram
   ```
   Both must be present and not expired.

3. **Test manually in the Pterodactyl console:**
   ```bash
   python -m gallery_dl --cookies /home/container/insta-cookies.txt \
     --config /home/container/gallery-dl.conf \
     -g "https://www.instagram.com/p/DaQTVzlABo1/"
   ```

4. **Datacenter IP block** — Instagram frequently blocks hosting provider IPs (Glacier Hosting, etc.) even with valid cookies. If step 3 fails on the server but works on your PC, the IP is likely blocked. Options:
   - Residential proxy (advanced)
   - Run gallery-dl on a machine with a residential IP instead of the panel

Upload `gallery-dl.conf` from the project root for better Instagram compatibility.

---

## Ongoing operations

| Task | How |
|------|-----|
| Update code | SFTP upload or git pull + restart |
| Update dependencies | Edit `requirements.txt` → restart |
| View logs | Panel console |
| Clear disk | Wait for auto-cleanup or delete `downloads/*` while stopped |
| Backup files | Copy `/home/container/downloads/` if needed |
