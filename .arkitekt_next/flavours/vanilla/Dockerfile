FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-21-jre \
    curl \
    build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
ENV PATH="$JAVA_HOME/bin:$PATH"

ENV MAVEN_VERSION=3.9.11
ENV MAVEN_HOME=/usr/share/maven
ENV MAVEN_CONFIG="/root/.m2"
ENV PATH="${MAVEN_HOME}/bin:$PATH"

# Install Maven
RUN mkdir -p /usr/share/maven \
    && curl -fsSL https://downloads.apache.org/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz \
    | tar -xz --strip-components=1 -C /usr/share/maven \
    && ln -s /usr/share/maven/bin/mvn /usr/bin/mvn

# Set up virtualenv (optional – Python already available)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
RUN pip install --no-cache-dir \
    "numpy>=1.21.0, <2" \
    "bioio" \
    "bioio-base" \
    "bioio-bioformats" \
    "bioio-lif" \
    "bioio-nd2" \
    "arkitekt-next[all]>=0.24"

# Copy and run your test script
COPY test.tiff z.py /tmp/
WORKDIR /tmp
RUN python z.py

# Copy your project
COPY . /workspace
WORKDIR /workspace
