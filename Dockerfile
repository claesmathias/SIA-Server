FROM python:3.12-slim

WORKDIR /opt/sia-server

# Install Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . ./

# Expose ports from sia-server.conf
EXPOSE 10000 10001

# Allow the config file to be mounted from the host
VOLUME ["/config"]

# Default command uses the mounted configuration file
CMD ["python3", "sia-server.py", "--config", "/config/sia-server.conf"]
