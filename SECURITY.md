# Security policy

Analysarr talks to your media server, Sonarr, Radarr and qBittorrent with their API keys, and can delete files. Security reports are taken seriously.

## Supported versions

Only the **latest release** receives security fixes. Please update before reporting.

## Reporting a vulnerability

**Do not open a public issue.** Report privately through GitHub:

**[Report a vulnerability](https://github.com/SEC844/Analysarr/security/advisories/new)**

Please include:

- the Analysarr version (Settings → Application),
- a description of the issue and its impact,
- steps to reproduce, or a proof of concept,
- any suggested fix.

Remove API keys, passwords, tokens and personal data from anything you share.

## What to expect

- An acknowledgement within a few days.
- An assessment and, if confirmed, a fix released as soon as possible, with a security note in the release.
- Credit in the release notes if you wish.

This is a community project maintained on a best-effort basis.

## Hardening recommendations

- Do not expose Analysarr directly to the internet. Use a reverse proxy with HTTPS, a VPN, or keep it on your local network.
- Use a strong administrator password.
- Give each service a dedicated API key you can revoke.
- Keep the Docker image up to date.
