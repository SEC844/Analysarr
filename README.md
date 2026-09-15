<p align="center">
  <img src="frontend/public/favicon.svg" width="96" height="96" alt="Analysarr">
</p>

<h1 align="center">Analysarr</h1>

<p align="center">
  <strong>The health dashboard for your media stack.</strong><br>
  Emby or Jellyfin · Sonarr · Radarr · qBittorrent · Seer · cross-seed
</p>

<p align="center">
  <a href="https://github.com/SEC844/Analysarr/releases/latest"><img src="https://img.shields.io/github/v/release/SEC844/Analysarr?label=release" alt="Latest release"></a>
  <a href="https://github.com/SEC844/Analysarr/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/SEC844/Analysarr/ci.yml?branch=dev&label=CI" alt="CI"></a>
  <a href="https://github.com/SEC844/Analysarr/pkgs/container/analysarr"><img src="https://img.shields.io/badge/docker-ghcr.io-2496ED?logo=docker&logoColor=white" alt="Docker image"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/SEC844/Analysarr" alt="License"></a>
</p>

<p align="center">
  <a href="README.fr.md">Lire en français</a>
</p>

---

Analysarr shows, for every movie and series, its state across your whole stack — and lets you act on it in a few clicks instead of juggling four web interfaces.

- Is this movie **still seeding**, and is the torrent **protected by a hardlink** to the library?
- Which files are **duplicates** left behind by a Sonarr/Radarr upgrade?
- Which torrents are **orphans** (old quality, nothing links to them anymore)?
- Who **watched** it, who **requested** it, and how much space would deleting it **really** free?

## Features

**Detection**
- Duplicates, orphan torrents, missing hardlinks, content seeded on a single tracker, media missing from the media server or not seeded at all.
- Torrent ↔ media matching by inode first (works for cross-seed copies living outside Sonarr/Radarr folders), then Sonarr/Radarr history, then title similarity.
- Trackers per torrent, with passkeys hidden.

**Actions — always with a preview and an explicit confirmation**
- Cascade cleanup of duplicates and orphans.
- Selective deletion (torrents, episodes, seasons, whole series or movie) with the **real** disk space freed, hardlinks accounted for.
- Optional removal from Sonarr/Radarr and Seer (never added to exclusion lists).
- One-click hardlink repair, with a symbolic link fallback across filesystems.
- Targeted cross-seed search per episode, season or whole series.

**Decision support**
- Watch activity per user (`3/10` watched it, progress on hover), last played date, date added.
- Seer requests: who asked, when, who approved.
- "Cleanup candidates" sort: big files nobody watched for a long time.

**Everyday comfort**
- Scheduled scans, scan history, path diagnostics that pinpoint a missing Docker mount.
- English and French interface, dark/light theme, display preferences.
- Update notification when a new version is released.

## Compatibility

| Service | Supported | Required |
|---|---|---|
| Emby | 4.x | One of Emby or Jellyfin |
| Jellyfin | 10.9 or newer | One of Emby or Jellyfin |
| Sonarr | v3, v4 | Yes |
| Radarr | v3 or newer | Yes |
| qBittorrent | 4.1 or newer (WebUI API v2) | Yes |
| Seer (Overseerr, Jellyseerr, Seerr) | Current versions | Optional |
| cross-seed | Daemon mode | Optional |

## Quick start

### Docker Compose

```yaml
services:
  analysarr:
    image: ghcr.io/sec844/analysarr:latest
    container_name: analysarr
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      DATABASE_PATH: /config/analysarr.db
    volumes:
      - ./analysarr:/config
      # Same host path AND same container path as in your media server,
      # qBittorrent, Sonarr and Radarr (see "Paths and hardlinks").
      - /mnt/data:/data
```

### Unraid

Docker → **Add Container** → paste this template URL:

```
https://raw.githubusercontent.com/SEC844/Analysarr/main/unraid/analysarr.xml
```

### First launch

Open `http://<host>:8000`, create the administrator account, then follow the setup wizard. There is no configuration file to edit: everything is configured from the interface, with a **Test connection** button for every service.

## Paths and hardlinks

This is the one thing to get right. Analysarr compares the files seen by your media server and by qBittorrent **from inside its own container**. It must therefore see **exactly the same paths** as those containers:

| Container | Host path | Container path |
|---|---|---|
| Emby / Jellyfin | `/mnt/data` | `/data` |
| qBittorrent | `/mnt/data` | `/data` |
| Sonarr / Radarr | `/mnt/data` | `/data` |
| **Analysarr** | `/mnt/data` | `/data` |

This is the layout recommended by the [TRaSH Guides](https://trash-guides.info/File-and-Folder-Structure/). If your containers use other paths, mirror them in Analysarr. **Settings → Paths → Path diagnostics** tells you immediately if a mount is missing, and which folder.

Write access to the data share is only used when you delete media or repair hardlinks, always after a confirmation.

## Updating

Pull the new image and recreate the container. Your settings and cache live in `/config` and are kept. The interface shows a notification when a new version is available (**Settings → Application**, can be disabled).

## Security

- A single administrator account; passwords hashed with bcrypt; login locked for 15 minutes after 5 failed attempts.
- Sessions stored server-side, sent as an `httpOnly` cookie.
- API keys and passwords of your services stay on the server: they are never sent back to the browser.
- The only outbound connections are the services you configure, plus an optional update check against the GitHub API (sends only the Analysarr version).
- Analysarr can delete files: do not expose it directly to the internet. Put it behind a reverse proxy with HTTPS, or keep it on your local network / VPN.

Found a vulnerability? Please report it privately, see [SECURITY.md](SECURITY.md).

## FAQ

**Will Analysarr delete something on its own?**
No. Scans are read-only. Every deletion or repair shows a preview first and waits for your confirmation.

**Torrents show as "not evaluated" or paths are unreachable.**
A mount is missing or differs from your other containers. Run **Settings → Paths → Path diagnostics**: it shows the folder that is not visible from Analysarr.

**The connection test says "the server at this address is Jellyfin, not Emby".**
Choose the right media server at the top of the media server card in Settings.

**Do I need Seer or cross-seed?**
No. Both are optional and fully hidden until you enable them.

## Contributing

Contributions are welcome — read [CONTRIBUTING.md](CONTRIBUTING.md) first.

## License

[GNU AGPL-3.0](LICENSE). You may use, modify and share Analysarr; any modified version you distribute or run as a service must stay open source under the same license.
