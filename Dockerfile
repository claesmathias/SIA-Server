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

ARG APP_VERSION=unknown
ARG BUILD_NUMBER=local
ARG COMMIT_SHA=dev
ENV APP_VERSION=$APP_VERSION \
    BUILD_NUMBER=$BUILD_NUMBER \
    COMMIT_SHA=$COMMIT_SHA

# Default command uses the mounted configuration file
CMD ["python3", "sia-server.py", "--config", "/config/sia-server.conf"]
