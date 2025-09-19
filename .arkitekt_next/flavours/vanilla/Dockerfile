# ---- Base Python image ----
FROM python:3.12-slim

# ---- System deps ----
# Add common build/runtime deps + git/ca-certs for fetching
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre \
    curl \
    ca-certificates \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# ---- Java/Maven env ----
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV PATH="$JAVA_HOME/bin:$PATH"

ENV MAVEN_VERSION=3.9.11
ENV MAVEN_HOME=/usr/share/maven
ENV MAVEN_CONFIG="/root/.m2"
ENV PATH="${MAVEN_HOME}/bin:$PATH"

# ---- Install Maven ----
RUN mkdir -p /usr/share/maven \
    && curl -fsSL https://downloads.apache.org/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz \
    | tar -xz --strip-components=1 -C /usr/share/maven \
    && ln -s /usr/share/maven/bin/mvn /usr/bin/mvn

# ---- Install uv (Astral) ----
# Installs to ~/.local/bin by default
RUN curl -LsSf https://astral.sh/uv/install.sh | sh -s -- 
ENV PATH="/root/.local/bin:${PATH}"

# Optional: let uv know which Python to use (from base image)
ENV UV_PYTHON=python3.12

# ---- Prepare workspace & dependency layer ----
# Copy only dependency files first to maximize Docker layer caching.
WORKDIR /workspace
# Copy pyproject + optional lockfile if present
COPY pyproject.toml ./
# Copy uv.lock if you have one; if not present it will be ignored at build time.
# (Using a wildcard avoids failing when uv.lock is missing.)
COPY uv.lock* ./

# Sync deps into a local .venv managed by uv (locked if uv.lock exists).
# If you build with BuildKit, the cache mount speeds this up; otherwise it's ignored.
# Use --frozen to enforce the lockfile if present.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --reinstall

# Make the project venv active for subsequent layers/commands
ENV VIRTUAL_ENV="/workspace/.venv"
ENV PATH="/workspace/.venv/bin:${PATH}"

# ---- (Optional) Bring in test assets and run quick check ----
# If your test expects files outside the repo, copy them now.


COPY test.tiff z.py /tmp/
# Run the test using the env created by uv
WORKDIR /tmp
RUN python /tmp/z.py

# ---- Copy the rest of the project ----
COPY . /workspace


# ---- Default working directory ----
WORKDIR /workspace

# ---- (Optional) Default command ----
# CMD ["bash"]
