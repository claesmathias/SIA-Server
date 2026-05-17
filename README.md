# Honeywell Galaxy SIA Notification Server

SIA-Server is a lightweight, self-hosted Python service that receives SIA protocol messages from Honeywell Galaxy Flex alarm systems and forwards them as rich, prioritized push notifications via Apprise.

It was created as a replacement for the discontinued free Honeywell push notification service, allowing users to regain full control over their alarm alerts without ongoing subscription costs.

This project was developed and tested on a Honeywell Galaxy Flex 20. It is likely compatible with other Honeywell Galaxy panels, but this has not been verified.

If your Galaxy Flex notifications suddenly stopped working, this project provides a self-hosted alternative.

> **IMPORTANT SECURITY NOTICE**
> By default, the communication between the alarm panel and this server is **unencrypted**. This server is designed to be run on a trusted local network only. Please read the full [Security & Privacy Guidelines](#security--privacy-guidelines) before installation.

## Features

-   **Self-Hosted:** Runs on any local Windows or Linux machine, like a Raspberry Pi.
-   **Real-time Notifications:** Instantly forwards alarm events to your devices.
-   **Prioritized Alerts:** Uses Apprise priorities to distinguish between urgent alarms and routine events.
-   **Advanced Notification Routing:** Route notifications for different accounts to different Apprise endpoints, each with its own optional authentication (Bearer Token or User/Pass).
-   **Robust Protocol Handling:** Correctly parses the multi-message protocol used by Galaxy Flex panels.
-   **Broad SIA Level Support:** The flexible parser can correctly handle event data from SIA Levels 0, 1, 2, and 3.
-   **Optional Heartbeat Server:** Includes an optional server to handle the proprietary Honeywell "IP Check" heartbeat.
-   **Connection Security Policies:** Per-account `ENABLED` policy (`Yes`/`No`/`Secure`) and a configurable `REJECT_POLICY` (`respond`/`drop`) to control how invalid connections are handled.
-   **Protocol State Machine:** Enforces correct SIA message ordering. Any connection that does not start with a valid `ACCOUNT_ID` is immediately rejected or silently dropped.
-   **Character Encoding Fixes:** Decodes the proprietary character set used by Galaxy panels (e.g., Å, Ä, Ö).
-   **Highly Configurable:** Most user settings are in a simple `sia-server.conf` file, with advanced protocol constants located in the `galaxy/` directory.

## Docker Compose Setup

This repository is designed to run using Docker Compose.
The compose stack builds the SIA server from this repository and runs it together with an Apprise notification container and an MQTT broker.

### Prerequisites

- Docker Engine installed.
- Docker Compose available.
- `sia-server.conf` present in the repository root.

### Start the stack

From the repository root:
```bash
docker compose up -d --build
```

### Check status

```bash
docker compose ps
```

### View logs

```bash
docker compose logs -f sia-server
```

### Stop the stack

```bash
docker compose down
```

### Enter the running SIA container

```bash
docker compose exec sia-server sh
```

### Mounting configuration

The compose file mounts the host `sia-server.conf` into the container at `/config/sia-server.conf`.
This means:

- edit `sia-server.conf` on the host,
- then restart the stack with `docker compose restart sia-server`.

### Docker Compose service notes

The `docker-compose.yml` file defines three services:

- `sia-server`: builds the local SIA server image and reads configuration from the mounted `/config/sia-server.conf`.
- `apprise`: provides the Apprise notification engine and stores state under `./apprise` on the host.
- `mqtt`: runs Mosquitto for MQTT endpoints used by Apprise.

The services share a Compose network, so internal hostnames like `apprise` and `mosquitto` can be used from the SIA server configuration.

If you need the Apprise web UI or API exposed, add a port mapping for the `apprise` service in `docker-compose.yml` and restart the stack.

### Example APPRISE_SERVICES for Docker

When using the bundled `apprise` and `mqtt` services, your SIA site configuration can reference container hostnames directly:

```ini
[023499]
SITE_NAME = My Home
ENABLED = Yes
APPRISE_ENABLED = Yes
APPRISE_TITLE = Galaxy Alarm
APPRISE_SERVICES = [
    'mqtt://apprise@mosquitto:1883/apprise',
    'whatsapp://<token>@<from_phone>/<to_phone>',
    'pover://<api_key>@<user_or_service_id>',
    'ntfys://public-topic',
    'ntfys://tk_<PRIVATE_TOKEN>@ntfy.example.com/private-topic',
    'hassio://<long_token>@homeassistant'
```

## Apprise Usage

The `apprise` container runs the Apprise notification engine for the SIA server.
Use the `apprise` service hostname in your `APPRISE_SERVICES` entries when the service is running inside the same Docker Compose network.

The container stores state and plugins in the `./apprise` folder on the host, so notification configuration survives container restarts.

If you want to access the Apprise web UI or API, add a port mapping to `docker-compose.yml` for the Apprise service and restart the compose stack.

## Testing with sia_server_tester.py

This repository includes `sia_server_tester.py`, a helper script that builds and sends raw Galaxy SIA packets to the server.
Use it from the host machine to verify the SIA server receives and processes panel messages.

Example using the built-in sample message:

```bash
python sia_server_tester.py --host 127.0.0.1 --port 10000 --send-sample
```

Example using the same payload shown in the README:

```bash
python sia_server_tester.py \
  --host 127.0.0.1 --port 10000 \
  --account-id 023499 \
  --new-event 'ti23:42/id023/pi013/CG' \
  --ascii ' PART SET USER' \
  --delay 0.05
```

If the Docker host is remote, replace `127.0.0.1` with the host IP address.

Example of raw hex segment mode:

```bash
python sia_server_tester.py \
  --segment 46233032333439399f \
  --segment 564e746932333a34322f69643032332f70693031332f4347fb \
  --segment 4e41205041525420534554205553455294 \
  --segment 40308f
```

## Configure Your Alarm Panel

Log into your Galaxy Flex panel's installer menu and configure the Ethernet module. The numbers in parentheses are the menu codes for a Galaxy Flex 20.
-   **ARC IP Address:** The IP of the machine running the Docker host (the container listens on port `10000`).
-   **ARC Port:** The port for the `[SIA-Server]` and optionally the `[IP-Check]` server. (Menu `56.1.1.1.4.1`)
-   **Protocol:** SIA. Levels 0-3 are supported; Level 3 is recommended for the most detail. (Menu `56.1.1.1.4.2`)
-   **Account Number:** Your 4 or 6-digit alarm account number. SIA Level 3 requires 6 digits. (Menu `56.1.2.1.1`)
-   **Encryption:** Set to **Off** unless you have `galaxy/encryption.py` installed. (Menu `56.3.3.5`)
-   **IP-Check:** (Optional) To use the heartbeat feature, enable it by setting a time interval (e.g., 00:30 for 30 minutes). `00:00` means disabled. (Menu `56.3.3.7.1`)
-   **Eng. Test:** Use this to send a test notification without generating a fault. (Menu `56.7.1`)

## Configure the Server

Edit `sia-server.conf` in the repository root to match your setup. The file is mounted into the container and read by the SIA server at startup.

```bash
nano sia-server.conf
```

If you change the configuration after the stack is running, restart only the SIA server container:

```bash
docker compose restart sia-server
```

## Configuration Explained

The primary configuration is done in `sia-server.conf`. This file is designed to be user-friendly and not sensitive to Python syntax. Advanced, technical constants are located in `galaxy/constants.py`.

-   **Site Sections (`[012345]`):** Each site is defined by a section where the header is the panel's unique **Account Number**.
    -   `SITE_NAME`: A friendly name for the site (e.g., "Main House"). If omitted, the account number is used.
    -   `ENABLED`: Controls the connection policy for this account. Accepts `Yes`, `No`, or `Secure`.
        -   `Yes` — Accept all connections to this account (default).
        -   `No` — Reject all connections from this account.
        -   `Secure` — Only accept encrypted connections. Plaintext connections will be rejected. You will need to supply `galaxy/encryption.py` to enable encryption.
    -   `APPRISE_ENABLED`, `APPRISE_TITLE`, `APPRISE_SERVICES`: Configure notification delivery for this site.
    -   `APPRISE_SERVICES` accepts one or more Apprise service URLs. Use tokens or credentials inside the service URL when needed.
-   **`[Default]` Section:** A special section for events from account numbers not specifically listed.
-   **`[SIA-Server]` Section:** Configure the ports and addresses for the main server.
    -   `REJECT_POLICY`: Controls how invalid or unauthorised connections are handled. Accepts `respond` or `drop`.
        -   `respond` — Send a SIA REJECT frame to the client (default).
        -   `drop` — Silently close the connection without sending anything.
-   **`[IP-Check]` Section:** Configure the ports and addresses for the optional heartbeat server.
    > **Note:** The IP Check server validates all incoming heartbeat packets before responding.
    > It verifies the packet length, and header. Invalid packets are dropped.

-   **`[Logging]` Section:** Control the log level and output destination.
    -   `LOG_LEVEL`: Set the verbosity of logs (`DEBUG`, `INFO`, `WARNING`, `ERROR`). `INFO` is recommended for normal use.
    -   `LOG_TO`: Choose `Screen`, `File`, or `Syslog`.
        -   `Screen` is best for manual testing and standard `systemd` services.
        -   `File` is best for creating a dedicated log file (e.g., on Windows or for `cron` jobs).
    -   **File-Specific Settings:** `LOG_FILE`, `LOG_MAX_MB`, and `LOG_BACKUP_COUNT` are only used when `LOG_TO = File`.

<details>
<summary><b>Advanced Logging: Using LOG_TO = Syslog</b></summary>

Setting `LOG_TO = Syslog` integrates the server's logging with the native operating system logger. This is an advanced option recommended for embedded systems like routers.

-   **Log Format:** When using `Syslog`, the server uses a simpler log format (`SIA-Server: LEVEL - Message`) because the `syslog` service adds its own timestamps and hostname.

-   **On Linux/Unix Systems:**
    -   The server will attempt to write to the standard `/dev/log` socket.
    -   For non-standard systems, you can specify a different path with the optional `SYSLOG_SOCKET` key.
    -   You can also change the `syslog` "facility" (which controls how the system `syslogd` categorizes the messages) using the optional `SYSLOG_FACILITY` key. Common values are `daemon` or `local0` through `local7`. This can be useful for tailoring log filtering rules on your specific system.

-   **On Windows Systems:**
    -   This will log messages to the **Windows Event Log** under the "Application" section with the source name "SIA-Server".
    -   **Dependencies:** This feature requires the `pywin32` package. You must install it from an **Administrator** prompt: `python -m pip install pywin32`.
    -   **Permissions:** The very first time you run the server with this option, it must be run **as an Administrator** to register the "SIA-Server" source in the Windows Registry. After that, it can be run as a normal user.
    -   If either the dependency is missing or the registration fails due to permissions, the server will print a clear warning and automatically fall back to logging to the screen.

</details>

-   **`[Notification]` Section:** Configures the server's resilient retry queue for handling network outages.
    -   `MAX_QUE_SIZE`, `MAX_RETRIES`, `MAX_RETRY_TIME` control the queue and retry behavior.
    -   `PRIORITY_1` through `PRIORITY_5`: Assign SIA Event Codes to different priority levels.
    -   `DEFAULT_PRIORITY`: The priority to use for any unlisted event code.

## Security & Privacy Guidelines
Please read these guidelines carefully.

**1. Local Network Communication (Panel to Server)**

The communication between your alarm panel and this server is **unencrypted**. Run it on a trusted local network (LAN).

> **Warning:** Do not expose the server's listening ports directly to the public internet. If you must, use a **VPN** (e.g., WireGuard).

**2. Notification Privacy (Server to Apprise endpoints)**

-   **Transport Security:** Apprise can send to HTTPS-backed endpoints, which is secure.
-   **Endpoint Privacy:** Many Apprise services are public by default. To secure them:
    -   **Use a long, unguessable topic or path.**
    -   **Use service authentication** when supported by the destination.
    -   **Use direct Apprise URLs** with embedded credentials or API tokens only when necessary.
    -   Alternatively: host your own private notification endpoint and point Apprise at it.

**Disclaimer:** You are ultimately responsible for securing your own setup.

## Acknowledgments
-   This project was developed through a collaborative effort with Anthropic's AI assistant, Claude.
-   The initial socket server structure was inspired by the [nimnull/sia-server](https://github.com/nimnull/sia-server) project.
-   Some protocol information was found in [dklemm/FlexSIA2MQTT](https://github.com/dklemm/FlexSIA2MQTT) project.

## License
This project is licensed under the MIT License.
